from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

import app.repository as repository_module
from app.repository import Repository


def amount_rows(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def empty_cost_rows() -> pd.DataFrame:
    return pd.DataFrame(columns=["user_id", "utc_date", "model", "wallet_type", "cost", "currency", "_source"])


class DashboardRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.repo = Repository(self.data_dir / "deepseek_usage.db", self.data_dir)
        self.repo.init_db()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def create_batch(self, batch_id: str) -> None:
        self.repo.create_import_batch(
            {
                "id": batch_id,
                "original_filename": f"{batch_id}.zip",
                "stored_path": f"{batch_id}.zip",
                "sha256": batch_id,
                "status": "PARSING",
                "uploaded_at": f"2026-05-15T10:00:0{batch_id[-1]}+08:00",
            }
        )

    def test_now_iso_uses_current_local_timezone(self) -> None:
        class FakeDateTime:
            @classmethod
            def now(cls, tz=None) -> datetime:
                if tz is not None:
                    raise AssertionError("now_iso should use the current local timezone")
                return datetime(2026, 5, 18, 20, 30, 1, tzinfo=ZoneInfo("Asia/Shanghai"))

        with patch.object(repository_module, "datetime", FakeDateTime):
            self.assertEqual(repository_module.now_iso(), "2026-05-18T20:30:01+08:00")

    def test_list_imports_is_paginated_with_twenty_rows_by_default(self) -> None:
        for index in range(25):
            batch_id = f"batch{index:02d}"
            self.repo.create_import_batch(
                {
                    "id": batch_id,
                    "original_filename": f"{batch_id}.zip",
                    "stored_path": f"{batch_id}.zip",
                    "sha256": batch_id,
                    "status": "SUCCESS",
                    "uploaded_at": f"2026-05-15T10:{index:02d}:00+08:00",
                }
            )

        first_page = self.repo.list_imports()
        second_page = self.repo.list_imports(page=2)

        self.assertEqual(first_page["total"], 25)
        self.assertEqual(first_page["page"], 1)
        self.assertEqual(first_page["page_size"], 20)
        self.assertEqual(len(first_page["items"]), 20)
        self.assertEqual(first_page["items"][0]["id"], "batch24")
        self.assertEqual(first_page["items"][-1]["id"], "batch05")
        self.assertEqual(second_page["total"], 25)
        self.assertEqual(second_page["page"], 2)
        self.assertEqual(len(second_page["items"]), 5)
        self.assertEqual(second_page["items"][0]["id"], "batch04")

    def test_dashboard_uses_latest_import_for_repeated_logical_usage_rows(self) -> None:
        self.create_batch("batch1")
        self.repo.save_import_data(
            "batch1",
            amount_rows(
                [
                    {
                        "user_id": "user-a",
                        "utc_date": "2026-05-01",
                        "model": "deepseek-chat",
                        "api_key_name": "prod-key",
                        "api_key": "sk-prod",
                        "type": "input_cache_miss_tokens",
                        "price": 0.000001,
                        "amount": 100,
                        "_source": "amount.csv",
                    }
                ]
            ),
            empty_cost_rows(),
            [],
        )

        self.create_batch("batch2")
        self.repo.save_import_data(
            "batch2",
            amount_rows(
                [
                    {
                        "user_id": "user-a",
                        "utc_date": "2026-05-01",
                        "model": "deepseek-chat",
                        "api_key_name": "prod-key",
                        "api_key": "sk-prod",
                        "type": "input_cache_miss_tokens",
                        "price": 0.000001,
                        "amount": 250,
                        "_source": "amount.csv",
                    }
                ]
            ),
            empty_cost_rows(),
            [],
        )

        data = self.repo.dashboard_data()

        self.assertEqual(data["kpi"]["total_tokens"], 250)
        self.assertEqual(data["by_model"][0]["tokens"], 250)
        self.assertEqual(data["by_key"][0]["tokens"], 250)

    def test_dashboard_exposes_latest_successful_import_time_as_data_updated_at(self) -> None:
        self.create_batch("batch1")
        self.repo.save_import_data(
            "batch1",
            amount_rows(
                [
                    {
                        "user_id": "user-a",
                        "utc_date": "2026-05-01",
                        "model": "deepseek-chat",
                        "api_key_name": "key-a",
                        "api_key": "sk-a",
                        "type": "input_cache_miss_tokens",
                        "price": 0.000001,
                        "amount": 100,
                        "_source": "amount.csv",
                    }
                ]
            ),
            empty_cost_rows(),
            [],
        )
        self.create_batch("batch2")
        self.repo.save_import_data(
            "batch2",
            amount_rows(
                [
                    {
                        "user_id": "user-a",
                        "utc_date": "2026-05-02",
                        "model": "deepseek-chat",
                        "api_key_name": "key-a",
                        "api_key": "sk-a",
                        "type": "input_cache_miss_tokens",
                        "price": 0.000001,
                        "amount": 120,
                        "_source": "amount.csv",
                    }
                ]
            ),
            empty_cost_rows(),
            [],
        )
        with self.repo.connect() as conn:
            conn.execute(
                "UPDATE import_batch SET parsed_at = ? WHERE id = ?",
                ("2026-05-19T21:35:00+08:00", "batch1"),
            )
            conn.execute(
                "UPDATE import_batch SET parsed_at = ? WHERE id = ?",
                ("2026-05-20T21:35:00+08:00", "batch2"),
            )

        data = self.repo.dashboard_data()

        self.assertEqual(data["data_updated_at"], "2026-05-20T21:35:00+08:00")

    def test_dashboard_data_update_time_falls_back_to_upload_time_when_parse_time_is_missing(self) -> None:
        self.create_batch("batch1")
        with self.repo.connect() as conn:
            conn.execute(
                """
                UPDATE import_batch
                   SET status = 'SUCCESS',
                       parsed_at = NULL,
                       uploaded_at = ?
                 WHERE id = ?
                """,
                ("2026-05-15T10:00:01+08:00", "batch1"),
            )

        data = self.repo.dashboard_data()

        self.assertEqual(data["data_updated_at"], "2026-05-15T10:00:01+08:00")

    def test_dashboard_materializes_active_usage_once_per_request(self) -> None:
        self.create_batch("batch1")
        self.repo.save_import_data(
            "batch1",
            amount_rows(
                [
                    {
                        "user_id": "user-a",
                        "utc_date": "2026-05-01",
                        "model": "deepseek-chat",
                        "api_key_name": "key-a",
                        "api_key": "sk-a",
                        "type": "input_cache_miss_tokens",
                        "price": 0.000001,
                        "amount": 100,
                        "_source": "amount.csv",
                    }
                ]
            ),
            empty_cost_rows(),
            [],
        )
        statements: list[str] = []

        class TracingRepository(Repository):
            @contextmanager
            def connect(inner_self):
                with super().connect() as conn:
                    conn.set_trace_callback(statements.append)
                    try:
                        yield conn
                    finally:
                        conn.set_trace_callback(None)

        tracing_repo = TracingRepository(self.data_dir / "deepseek_usage.db", self.data_dir)

        tracing_repo.dashboard_data()

        trace_sql = "\n".join(statements)
        self.assertEqual(trace_sql.count("CREATE TEMP TABLE active_amount"), 1)
        self.assertNotIn("WITH active_amount", trace_sql)

    def test_dashboard_groups_key_rank_by_key_name_and_exposes_model_breakdowns(self) -> None:
        self.create_batch("batch1")
        self.repo.save_import_data(
            "batch1",
            amount_rows(
                [
                    {
                        "user_id": "user-a",
                        "utc_date": "2026-05-01",
                        "model": "deepseek-chat",
                        "api_key_name": "shared-key",
                        "api_key": "sk-a",
                        "type": "input_cache_hit_tokens",
                        "price": 0.0000001,
                        "amount": 10,
                        "_source": "amount.csv",
                    },
                    {
                        "user_id": "user-b",
                        "utc_date": "2026-05-01",
                        "model": "deepseek-reasoner",
                        "api_key_name": "shared-key",
                        "api_key": "sk-b",
                        "type": "output_tokens",
                        "price": 0.000001,
                        "amount": 40,
                        "_source": "amount.csv",
                    },
                    {
                        "user_id": "user-b",
                        "utc_date": "2026-05-02",
                        "model": "deepseek-reasoner",
                        "api_key_name": "other-key",
                        "api_key": "sk-c",
                        "type": "input_cache_miss_tokens",
                        "price": 0.0000005,
                        "amount": 30,
                        "_source": "amount.csv",
                    },
                ]
            ),
            empty_cost_rows(),
            [],
        )

        data = self.repo.dashboard_data()

        self.assertEqual(data["by_key"][0]["key_name"], "shared-key")
        self.assertEqual(data["by_key"][0]["tokens"], 50)
        self.assertEqual(data["by_key"][0]["account_count"], 2)
        self.assertEqual(data["by_key"][0]["cache_hit_tokens"], 10)
        self.assertEqual(data["by_key"][0]["cache_miss_tokens"], 0)
        self.assertEqual(data["by_key"][0]["output_tokens"], 40)
        chat_key_model = next(
            row
            for row in data["by_key_model"]
            if row["key_name"] == "shared-key" and row["model"] == "deepseek-chat"
        )
        self.assertEqual(chat_key_model["account_name"], "未命名账号-user-a")
        self.assertEqual(chat_key_model["account_count"], 1)
        self.assertEqual(chat_key_model["tokens"], 10)
        self.assertAlmostEqual(chat_key_model["cost"], 0.000001)
        self.assertEqual(chat_key_model["cache_hit_tokens"], 10)
        self.assertEqual(chat_key_model["cache_miss_tokens"], 0)
        self.assertEqual(chat_key_model["output_tokens"], 0)

        reasoner_key_model = next(
            row
            for row in data["by_key_model"]
            if row["key_name"] == "shared-key" and row["model"] == "deepseek-reasoner"
        )
        self.assertEqual(reasoner_key_model["account_name"], "未命名账号-user-b")
        self.assertEqual(reasoner_key_model["account_count"], 1)
        self.assertEqual(reasoner_key_model["tokens"], 40)
        self.assertAlmostEqual(reasoner_key_model["cost"], 0.00004)
        self.assertEqual(reasoner_key_model["cache_hit_tokens"], 0)
        self.assertEqual(reasoner_key_model["cache_miss_tokens"], 0)
        self.assertEqual(reasoner_key_model["output_tokens"], 40)
        self.assertIn("cost_per_million_tokens", data["by_model"][0])
        self.assertIn("cache_hit_tokens", data["by_model"][0])
        self.assertIn("cache_miss_tokens", data["by_model"][0])
        self.assertIn("output_tokens", data["by_model"][0])
        self.assertIn({"model": "deepseek-chat", "type": "input_cache_hit_tokens", "amount": 10}, data["token_mix"])
        self.assertTrue(
            any(
                row["utc_date"] == "2026-05-01"
                and row["model"] == "deepseek-reasoner"
                and row["tokens"] == 40
                and row["output_tokens"] == 40
                for row in data["trend_by_model"]
            )
        )
        self.assertTrue(
            any(
                row["utc_date"] == "2026-05-01"
                and "model" not in row
                and row["tokens"] == 50
                and row["cache_hit_tokens"] == 10
                and row["output_tokens"] == 40
                for row in data["trend"]
            )
        )

    def test_dashboard_marks_single_account_mode_from_global_usage(self) -> None:
        self.create_batch("batch1")
        self.repo.save_import_data(
            "batch1",
            amount_rows(
                [
                    {
                        "user_id": "user-a",
                        "utc_date": "2026-05-01",
                        "model": "deepseek-chat",
                        "api_key_name": "prod-key",
                        "api_key": "sk-prod",
                        "type": "input_cache_miss_tokens",
                        "price": 0.000001,
                        "amount": 100,
                        "_source": "amount.csv",
                    }
                ]
            ),
            empty_cost_rows(),
            [],
        )

        data = self.repo.dashboard_data()

        self.assertEqual(data["account_mode"], "single")
        self.assertEqual(data["global_account_count"], 1)

    def test_dashboard_account_mode_uses_global_accounts_not_current_filter(self) -> None:
        self.create_batch("batch1")
        self.repo.save_import_data(
            "batch1",
            amount_rows(
                [
                    {
                        "user_id": "user-a",
                        "utc_date": "2026-05-01",
                        "model": "deepseek-chat",
                        "api_key_name": "key-a",
                        "api_key": "sk-a",
                        "type": "input_cache_miss_tokens",
                        "price": 0.000001,
                        "amount": 100,
                        "_source": "amount.csv",
                    },
                    {
                        "user_id": "user-b",
                        "utc_date": "2026-05-02",
                        "model": "deepseek-chat",
                        "api_key_name": "key-b",
                        "api_key": "sk-b",
                        "type": "output_tokens",
                        "price": 0.000002,
                        "amount": 50,
                        "_source": "amount.csv",
                    },
                ]
            ),
            empty_cost_rows(),
            [],
        )

        data = self.repo.dashboard_data(user_id="user-a")

        self.assertEqual(data["kpi"]["account_count"], 1)
        self.assertEqual(data["account_mode"], "multiple")
        self.assertEqual(data["global_account_count"], 2)


if __name__ == "__main__":
    unittest.main()
