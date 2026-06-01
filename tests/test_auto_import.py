from __future__ import annotations

import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.auto_import import AutoImportScheduler, import_usage_archive, next_daily_run, parse_curl_command, run_auto_import_once
from app.repository import Repository


class AutoImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.repo = Repository(self.data_dir / "deepseek_usage.db", self.data_dir)
        self.repo.init_db()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def create_usage_zip_without_user_id(self) -> Path:
        usage_zip = self.data_dir / "usage.zip"
        with zipfile.ZipFile(usage_zip, "w") as archive:
            archive.writestr(
                "amount.csv",
                "\n".join(
                    [
                        "utc_date,model,api_key_name,api_key,type,price,amount",
                        "2026-05-01,deepseek-chat,prod-key,sk-prod,input_cache_miss_tokens,0.000001,100",
                        "2026-05-01,deepseek-chat,prod-key,sk-prod,request_count,0,2",
                    ]
                ),
            )
            archive.writestr(
                "cost.csv",
                "\n".join(
                    [
                        "utc_date,model,wallet_type,cost,currency",
                        "2026-05-01,deepseek-chat,main,0.01,CNY",
                    ]
                ),
            )
        return usage_zip

    def test_import_usage_archive_fills_default_user_id_for_single_account_export(self) -> None:
        result = import_usage_archive(
            repo=self.repo,
            data_dir=self.data_dir,
            tmp_extract_dir=self.data_dir / "tmp" / "extract",
            archive_path=self.create_usage_zip_without_user_id(),
            original_filename="usage.zip",
            default_user_id="deepseek-main",
            batch_prefix="auto",
        )

        data = self.repo.dashboard_data()

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(data["account_mode"], "single")
        self.assertEqual(data["global_account_count"], 1)
        self.assertEqual(data["by_account"][0]["user_id"], "deepseek-main")
        self.assertEqual(data["by_key"][0]["account_name"], "未命名账号-deepseek")

    def test_parse_curl_command_redacts_sensitive_headers_in_summary(self) -> None:
        request = parse_curl_command(
            "curl 'https://platform.deepseek.com/api/usage/export?month=2026-05' "
            "-H 'accept: application/zip' "
            "-H 'cookie: sessionid=secret-value; csrf=secret-csrf' "
            "-H 'x-csrf-token: token-value' "
            "--compressed"
        )

        self.assertEqual(request.url, "https://platform.deepseek.com/api/usage/export?month=2026-05")
        self.assertEqual(request.headers["accept"], "application/zip")
        self.assertEqual(request.headers["cookie"], "sessionid=secret-value; csrf=secret-csrf")
        self.assertEqual(request.headers["x-csrf-token"], "token-value")
        self.assertIn("cookie=<redacted>", request.safe_summary())
        self.assertIn("x-csrf-token=<redacted>", request.safe_summary())
        self.assertNotIn("secret-value", request.safe_summary())
        self.assertNotIn("token-value", request.safe_summary())

    def test_next_daily_run_uses_configured_local_time(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")

        today = next_daily_run(datetime(2026, 5, 18, 19, 0, tzinfo=tz), "20:30", "Asia/Shanghai")
        tomorrow = next_daily_run(datetime(2026, 5, 18, 21, 0, tzinfo=tz), "20:30", "Asia/Shanghai")

        self.assertEqual(today.isoformat(), "2026-05-18T20:30:00+08:00")
        self.assertEqual(tomorrow.isoformat(), "2026-05-19T20:30:00+08:00")

    def test_run_auto_import_once_reads_curl_secret_and_imports_downloaded_archive(self) -> None:
        curl_file = self.data_dir / "secrets" / "deepseek-export.curl"
        curl_file.parent.mkdir(parents=True)
        curl_file.write_text(
            "curl 'https://platform.deepseek.com/api/usage/export?month=2026-05' "
            "-H 'cookie: sessionid=secret-value'",
            encoding="utf-8",
        )

        def fake_downloader(curl_command: str, target_dir: Path) -> Path:
            self.assertIn("secret-value", curl_command)
            self.assertTrue(target_dir.exists())
            return self.create_usage_zip_without_user_id()

        result = run_auto_import_once(
            repo=self.repo,
            data_dir=self.data_dir,
            tmp_extract_dir=self.data_dir / "tmp" / "extract",
            curl_file=curl_file,
            default_user_id="deepseek-main",
            downloader=fake_downloader,
        )

        data = self.repo.dashboard_data()

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(data["global_account_count"], 1)
        self.assertEqual(data["by_account"][0]["user_id"], "deepseek-main")

    def test_run_auto_import_once_refreshes_export_month_from_configured_timezone(self) -> None:
        curl_file = self.data_dir / "secrets" / "deepseek-export.curl"
        curl_file.parent.mkdir(parents=True)
        curl_file.write_text(
            "curl 'https://platform.deepseek.com/api/v0/usage/export?month=5&year=2026' "
            "-H 'cookie: sessionid=secret-value'",
            encoding="utf-8",
        )

        def fake_downloader(curl_command: str, target_dir: Path) -> Path:
            self.assertIn("sessionid=secret-value", curl_command)
            self.assertIn("month=6", curl_command)
            self.assertIn("year=2026", curl_command)
            self.assertNotIn("month=5", curl_command)
            return self.create_usage_zip_without_user_id()

        result = run_auto_import_once(
            repo=self.repo,
            data_dir=self.data_dir,
            tmp_extract_dir=self.data_dir / "tmp" / "extract",
            curl_file=curl_file,
            default_user_id="deepseek-main",
            export_timezone_name="Asia/Shanghai",
            current_time=datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            downloader=fake_downloader,
        )

        self.assertEqual(result["status"], "SUCCESS")

    def test_run_auto_import_once_requires_default_user_id(self) -> None:
        curl_file = self.data_dir / "deepseek-export.curl"
        curl_file.write_text("curl 'https://platform.deepseek.com/api/usage/export'", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "DEEPSEEK_SINGLE_ACCOUNT_USER_ID"):
            run_auto_import_once(
                repo=self.repo,
                data_dir=self.data_dir,
                tmp_extract_dir=self.data_dir / "tmp" / "extract",
                curl_file=curl_file,
                default_user_id="",
                downloader=lambda _curl_command, _target_dir: self.create_usage_zip_without_user_id(),
            )

    def test_scheduler_run_once_records_redacted_failure_status(self) -> None:
        def failing_job() -> dict[str, object]:
            raise RuntimeError("download failed with cookie=sessionid=secret-value")

        scheduler = AutoImportScheduler(job=failing_job, daily_time="20:30", timezone_name="Asia/Shanghai")

        result = scheduler.run_once()
        status = scheduler.status()

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(status["last_status"], "FAILED")
        self.assertIn("<redacted>", status["last_error"])
        self.assertNotIn("secret-value", status["last_error"])


if __name__ == "__main__":
    unittest.main()
