from __future__ import annotations

import csv
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Repository:
    def __init__(self, db_path: Path, data_dir: Path) -> None:
        self.db_path = db_path
        self.data_dir = data_dir

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS account_mapping (
                    user_id TEXT PRIMARY KEY,
                    account_name TEXT NOT NULL,
                    owner TEXT,
                    department TEXT,
                    remark TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS import_batch (
                    id TEXT PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    min_utc_date TEXT,
                    max_utc_date TEXT,
                    amount_row_count INTEGER NOT NULL DEFAULT 0,
                    cost_row_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    uploaded_at TEXT NOT NULL,
                    parsed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS usage_amount (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_batch_id TEXT NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
                    user_id TEXT,
                    utc_date TEXT NOT NULL,
                    model TEXT NOT NULL,
                    api_key_name TEXT,
                    api_key TEXT,
                    type TEXT NOT NULL,
                    price REAL,
                    amount REAL NOT NULL,
                    source TEXT
                );

                CREATE TABLE IF NOT EXISTS usage_cost (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_batch_id TEXT NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
                    user_id TEXT,
                    utc_date TEXT NOT NULL,
                    model TEXT NOT NULL,
                    wallet_type TEXT,
                    cost REAL NOT NULL,
                    currency TEXT,
                    source TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_usage_amount_filters
                    ON usage_amount(user_id, utc_date, model, api_key_name, api_key, type);
                CREATE INDEX IF NOT EXISTS idx_usage_cost_filters
                    ON usage_cost(user_id, utc_date, model, currency);
                CREATE INDEX IF NOT EXISTS idx_import_batch_uploaded_at
                    ON import_batch(uploaded_at);
                """
            )

    def create_import_batch(self, batch: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO import_batch (
                    id, original_filename, stored_path, sha256, status, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["id"],
                    batch["original_filename"],
                    batch["stored_path"],
                    batch["sha256"],
                    batch["status"],
                    batch["uploaded_at"],
                ),
            )

    def find_import_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM import_batch WHERE sha256 = ?", (sha256,)).fetchone()
        return dict(row) if row else None

    def mark_import_failed(self, batch_id: str, error_message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE import_batch
                   SET status = 'FAILED', error_message = ?, parsed_at = ?
                 WHERE id = ?
                """,
                (error_message[:4000], now_iso(), batch_id),
            )

    def save_import_data(
        self,
        batch_id: str,
        amount: pd.DataFrame,
        cost: pd.DataFrame,
        warnings: list[str],
    ) -> None:
        date_values: list[str] = []
        if not amount.empty:
            date_values.extend(amount["utc_date"].dropna().astype(str).tolist())
        if not cost.empty:
            date_values.extend(cost["utc_date"].dropna().astype(str).tolist())

        with self.connect() as conn:
            conn.execute("DELETE FROM usage_amount WHERE import_batch_id = ?", (batch_id,))
            conn.execute("DELETE FROM usage_cost WHERE import_batch_id = ?", (batch_id,))

            if not amount.empty:
                conn.executemany(
                    """
                    INSERT INTO usage_amount (
                        import_batch_id, user_id, utc_date, model, api_key_name,
                        api_key, type, price, amount, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch_id,
                            _str(row.get("user_id")),
                            _str(row.get("utc_date")),
                            _str(row.get("model")),
                            _str(row.get("api_key_name")),
                            _str(row.get("api_key")),
                            _str(row.get("type")),
                            _float_or_none(row.get("price")),
                            float(row.get("amount")),
                            _str(row.get("_source")),
                        )
                        for row in amount.to_dict(orient="records")
                    ],
                )

            if not cost.empty:
                conn.executemany(
                    """
                    INSERT INTO usage_cost (
                        import_batch_id, user_id, utc_date, model,
                        wallet_type, cost, currency, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch_id,
                            _str(row.get("user_id")),
                            _str(row.get("utc_date")),
                            _str(row.get("model")),
                            _str(row.get("wallet_type")),
                            float(row.get("cost")),
                            _str(row.get("currency")),
                            _str(row.get("_source")),
                        )
                        for row in cost.to_dict(orient="records")
                    ],
                )

            self._ensure_account_rows(conn, _distinct_user_ids(amount, cost))

            conn.execute(
                """
                UPDATE import_batch
                   SET status = 'SUCCESS',
                       min_utc_date = ?,
                       max_utc_date = ?,
                       amount_row_count = ?,
                       cost_row_count = ?,
                       error_message = ?,
                       parsed_at = ?
                 WHERE id = ?
                """,
                (
                    min(date_values) if date_values else None,
                    max(date_values) if date_values else None,
                    int(len(amount)),
                    int(len(cost)),
                    "\n".join(warnings) if warnings else None,
                    now_iso(),
                    batch_id,
                ),
            )

    def _ensure_account_rows(self, conn: sqlite3.Connection, user_ids: set[str]) -> None:
        ts = now_iso()
        for user_id in sorted(user_ids):
            if not user_id:
                continue
            exists = conn.execute("SELECT 1 FROM account_mapping WHERE user_id = ?", (user_id,)).fetchone()
            if exists:
                continue
            label = user_id[:8] if len(user_id) >= 8 else user_id
            conn.execute(
                """
                INSERT INTO account_mapping (
                    user_id, account_name, owner, department, remark, enabled, created_at, updated_at
                ) VALUES (?, ?, '', '', '自动发现，待维护', 1, ?, ?)
                """,
                (user_id, f"未命名账号-{label}", ts, ts),
            )

    def dashboard_data(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        user_id: str | None = None,
        model: str | None = None,
        api_key_query: str | None = None,
        department: str | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        amount_where, amount_params = _build_filters(
            "a", date_from, date_to, user_id, model, api_key_query, department, owner
        )

        with self.connect() as conn:
            kpi = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN type <> 'request_count' THEN amount ELSE 0 END), 0) AS total_tokens,
                    COALESCE(SUM(CASE WHEN type = 'request_count' THEN amount ELSE 0 END), 0) AS total_requests,
                    COALESCE(SUM(CASE WHEN type <> 'request_count' THEN COALESCE(price, 0) * amount ELSE 0 END), 0) AS total_cost,
                    COALESCE(SUM(CASE WHEN type IN ('input_cache_hit_tokens', 'input_cache_miss_tokens') THEN amount ELSE 0 END), 0) AS input_tokens,
                    COALESCE(SUM(CASE WHEN type = 'input_cache_hit_tokens' THEN amount ELSE 0 END), 0) AS cache_hit_tokens,
                    COALESCE(SUM(CASE WHEN type = 'input_cache_miss_tokens' THEN amount ELSE 0 END), 0) AS cache_miss_tokens,
                    COALESCE(SUM(CASE WHEN type = 'output_tokens' THEN amount ELSE 0 END), 0) AS output_tokens,
                    COUNT(DISTINCT COALESCE(NULLIF(api_key_name, ''), NULLIF(api_key, ''))) AS key_count,
                    COUNT(DISTINCT user_id) AS account_count,
                    COUNT(DISTINCT model) AS model_count,
                    COUNT(DISTINCT utc_date) AS day_count
                  FROM usage_amount a
                 WHERE {amount_where}
                """,
                amount_params,
            ).fetchone()
            by_account = _rows(
                conn.execute(
                    f"""
                    SELECT
                        COALESCE(m.account_name, a.user_id, '未知账号') AS account_name,
                        a.user_id,
                        COALESCE(NULLIF(TRIM(m.owner), ''), '未维护负责人') AS owner,
                        COALESCE(NULLIF(TRIM(m.department), ''), '未维护部门') AS department,
                        COALESCE(SUM(CASE WHEN a.type = 'request_count' THEN a.amount ELSE 0 END), 0) AS requests,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN a.amount ELSE 0 END), 0) AS tokens,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN COALESCE(a.price, 0) * a.amount ELSE 0 END), 0) AS cost
                      FROM usage_amount a
                      LEFT JOIN account_mapping m ON m.user_id = a.user_id
                     WHERE {amount_where}
                     GROUP BY a.user_id, account_name, owner, department
                     ORDER BY tokens DESC, requests DESC
                     LIMIT 50
                    """,
                    amount_params,
                )
            )
            by_key = _rows(
                conn.execute(
                    f"""
                    SELECT
                        COALESCE(NULLIF(a.api_key_name, ''), NULLIF(a.api_key, ''), '未命名 Key') AS key_name,
                        COALESCE(m.account_name, a.user_id, '未知账号') AS account_name,
                        a.user_id,
                        COALESCE(SUM(CASE WHEN a.type = 'request_count' THEN a.amount ELSE 0 END), 0) AS requests,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN a.amount ELSE 0 END), 0) AS tokens,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN COALESCE(a.price, 0) * a.amount ELSE 0 END), 0) AS cost
                      FROM usage_amount a
                      LEFT JOIN account_mapping m ON m.user_id = a.user_id
                     WHERE {amount_where}
                     GROUP BY a.user_id, key_name, account_name
                     ORDER BY tokens DESC, requests DESC
                     LIMIT 100
                    """,
                    amount_params,
                )
            )
            by_model = _rows(
                conn.execute(
                    f"""
                    SELECT
                        a.model,
                        COALESCE(SUM(CASE WHEN a.type = 'request_count' THEN a.amount ELSE 0 END), 0) AS requests,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN a.amount ELSE 0 END), 0) AS tokens,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN COALESCE(a.price, 0) * a.amount ELSE 0 END), 0) AS cost
                      FROM usage_amount a
                     WHERE {amount_where}
                     GROUP BY a.model
                     ORDER BY tokens DESC, requests DESC
                    """,
                    amount_params,
                )
            )
            by_department = _rows(
                conn.execute(
                    f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(m.department), ''), '未维护部门') AS department,
                        COUNT(DISTINCT a.user_id) AS account_count,
                        COALESCE(SUM(CASE WHEN a.type = 'request_count' THEN a.amount ELSE 0 END), 0) AS requests,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN a.amount ELSE 0 END), 0) AS tokens,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN COALESCE(a.price, 0) * a.amount ELSE 0 END), 0) AS cost
                      FROM usage_amount a
                      LEFT JOIN account_mapping m ON m.user_id = a.user_id
                     WHERE {amount_where}
                     GROUP BY department
                     ORDER BY cost DESC, tokens DESC, requests DESC
                     LIMIT 30
                    """,
                    amount_params,
                )
            )
            by_owner = _rows(
                conn.execute(
                    f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(m.owner), ''), '未维护负责人') AS owner,
                        COUNT(DISTINCT a.user_id) AS account_count,
                        COALESCE(SUM(CASE WHEN a.type = 'request_count' THEN a.amount ELSE 0 END), 0) AS requests,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN a.amount ELSE 0 END), 0) AS tokens,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN COALESCE(a.price, 0) * a.amount ELSE 0 END), 0) AS cost
                      FROM usage_amount a
                      LEFT JOIN account_mapping m ON m.user_id = a.user_id
                     WHERE {amount_where}
                     GROUP BY owner
                     ORDER BY cost DESC, tokens DESC, requests DESC
                     LIMIT 30
                    """,
                    amount_params,
                )
            )
            token_mix = _rows(
                conn.execute(
                    f"""
                    SELECT
                        a.type,
                        COALESCE(SUM(a.amount), 0) AS amount
                      FROM usage_amount a
                     WHERE {amount_where}
                     GROUP BY a.type
                     ORDER BY amount DESC
                    """,
                    amount_params,
                )
            )
            model_account = _rows(
                conn.execute(
                    f"""
                    SELECT
                        COALESCE(m.account_name, a.user_id, '未知账号') AS account_name,
                        a.user_id,
                        a.model,
                        COALESCE(SUM(CASE WHEN a.type = 'request_count' THEN a.amount ELSE 0 END), 0) AS requests,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN a.amount ELSE 0 END), 0) AS tokens,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN COALESCE(a.price, 0) * a.amount ELSE 0 END), 0) AS cost
                      FROM usage_amount a
                      LEFT JOIN account_mapping m ON m.user_id = a.user_id
                     WHERE {amount_where}
                     GROUP BY a.user_id, account_name, a.model
                     ORDER BY tokens DESC, requests DESC
                     LIMIT 80
                    """,
                    amount_params,
                )
            )
            trend = _rows(
                conn.execute(
                    f"""
                    SELECT
                        a.utc_date,
                        COALESCE(SUM(CASE WHEN a.type = 'request_count' THEN a.amount ELSE 0 END), 0) AS requests,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN a.amount ELSE 0 END), 0) AS tokens,
                        COALESCE(SUM(CASE WHEN a.type <> 'request_count' THEN COALESCE(a.price, 0) * a.amount ELSE 0 END), 0) AS cost,
                        COALESCE(SUM(CASE WHEN a.type IN ('input_cache_hit_tokens', 'input_cache_miss_tokens') THEN a.amount ELSE 0 END), 0) AS input_tokens,
                        COALESCE(SUM(CASE WHEN a.type = 'output_tokens' THEN a.amount ELSE 0 END), 0) AS output_tokens
                      FROM usage_amount a
                     WHERE {amount_where}
                     GROUP BY a.utc_date
                     ORDER BY a.utc_date
                    """,
                    amount_params,
                )
            )
            accounts = _rows(
                conn.execute(
                    """
                    SELECT user_id, account_name, owner, department, remark, enabled
                      FROM account_mapping
                     ORDER BY enabled DESC, account_name, user_id
                    """
                )
            )
            models = [row["model"] for row in conn.execute("SELECT DISTINCT model FROM usage_amount ORDER BY model")]
            departments = [
                row["department"]
                for row in conn.execute(
                    """
                    SELECT DISTINCT COALESCE(NULLIF(TRIM(department), ''), '未维护部门') AS department
                      FROM account_mapping
                     ORDER BY department
                    """
                )
            ]
            owners = [
                row["owner"]
                for row in conn.execute(
                    """
                    SELECT DISTINCT COALESCE(NULLIF(TRIM(owner), ''), '未维护负责人') AS owner
                      FROM account_mapping
                     ORDER BY owner
                    """
                )
            ]

        return {
            "kpi": {
                "total_tokens": kpi["total_tokens"],
                "total_requests": kpi["total_requests"],
                "total_cost": kpi["total_cost"],
                "key_count": kpi["key_count"],
                "account_count": kpi["account_count"],
                "model_count": kpi["model_count"],
                "day_count": kpi["day_count"],
                "input_tokens": kpi["input_tokens"],
                "cache_hit_tokens": kpi["cache_hit_tokens"],
                "cache_miss_tokens": kpi["cache_miss_tokens"],
                "output_tokens": kpi["output_tokens"],
            },
            "by_account": by_account,
            "by_department": by_department,
            "by_owner": by_owner,
            "by_key": by_key,
            "by_model": by_model,
            "token_mix": token_mix,
            "model_account": model_account,
            "trend": trend,
            "accounts": accounts,
            "models": models,
            "departments": departments,
            "owners": owners,
        }

    def list_imports(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return _rows(
                conn.execute(
                    """
                    SELECT id, original_filename, stored_path, sha256, status,
                           min_utc_date, max_utc_date, amount_row_count,
                           cost_row_count, error_message, uploaded_at, parsed_at
                      FROM import_batch
                     ORDER BY uploaded_at DESC
                     LIMIT 100
                    """
                )
            )

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return _rows(
                conn.execute(
                    """
                    SELECT user_id, account_name, owner, department, remark, enabled, created_at, updated_at
                      FROM account_mapping
                     ORDER BY enabled DESC, account_name, user_id
                    """
                )
            )

    def update_account(self, user_id: str, data: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE account_mapping
                   SET account_name = ?,
                       owner = ?,
                       department = ?,
                       remark = ?,
                       enabled = ?,
                       updated_at = ?
                 WHERE user_id = ?
                """,
                (
                    data.get("account_name") or user_id,
                    data.get("owner") or "",
                    data.get("department") or "",
                    data.get("remark") or "",
                    1 if data.get("enabled", True) else 0,
                    now_iso(),
                    user_id,
                ),
            )

    def import_accounts_csv(self, path: Path) -> int:
        count = 0
        ts = now_iso()
        with path.open("r", encoding="utf-8-sig", newline="") as handle, self.connect() as conn:
            reader = csv.DictReader(handle)
            for row in reader:
                user_id = (row.get("user_id") or "").strip()
                if not user_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO account_mapping (
                        user_id, account_name, owner, department, remark, enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        account_name = excluded.account_name,
                        owner = excluded.owner,
                        department = excluded.department,
                        remark = excluded.remark,
                        enabled = excluded.enabled,
                        updated_at = excluded.updated_at
                    """,
                    (
                        user_id,
                        (row.get("account_name") or user_id).strip(),
                        (row.get("owner") or "").strip(),
                        (row.get("department") or "").strip(),
                        (row.get("remark") or "").strip(),
                        int((row.get("enabled") or "1").strip() not in {"0", "false", "False", "否"}),
                        ts,
                        ts,
                    ),
                )
                count += 1
        return count

    def delete_import(self, batch_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT stored_path FROM import_batch WHERE id = ?", (batch_id,)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM import_batch WHERE id = ?", (batch_id,))
        stored = self.data_dir / row["stored_path"]
        if stored.exists():
            stored.unlink()
        return True

    def cleanup_uploads(self, retention_days: int) -> int:
        cutoff = datetime.now(timezone.utc).astimezone().timestamp() - retention_days * 86400
        removed = 0
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, stored_path, uploaded_at
                  FROM import_batch
                 WHERE status = 'SUCCESS'
                """
            ).fetchall()
        for row in rows:
            stored = self.data_dir / row["stored_path"]
            if not stored.exists():
                continue
            if stored.stat().st_mtime < cutoff:
                stored.unlink()
                removed += 1
        return removed


def _str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value)


def _float_or_none(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _distinct_user_ids(amount: pd.DataFrame, cost: pd.DataFrame) -> set[str]:
    values: set[str] = set()
    if not amount.empty:
        values.update(amount["user_id"].dropna().astype(str).str.strip().tolist())
    if not cost.empty:
        values.update(cost["user_id"].dropna().astype(str).str.strip().tolist())
    return {value for value in values if value}


def _build_filters(
    alias: str,
    date_from: str | None,
    date_to: str | None,
    user_id: str | None,
    model: str | None,
    api_key_query: str | None,
    department: str | None,
    owner: str | None,
) -> tuple[str, list[Any]]:
    conditions = ["1 = 1"]
    params: list[Any] = []
    if date_from:
        conditions.append(f"{alias}.utc_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append(f"{alias}.utc_date <= ?")
        params.append(date_to)
    if user_id:
        conditions.append(f"{alias}.user_id = ?")
        params.append(user_id)
    if model:
        conditions.append(f"{alias}.model = ?")
        params.append(model)
    if api_key_query:
        conditions.append(f"(COALESCE({alias}.api_key_name, '') LIKE ? OR COALESCE({alias}.api_key, '') LIKE ?)")
        like = f"%{api_key_query}%"
        params.extend([like, like])
    if department:
        conditions.append(
            f"""
            EXISTS (
                SELECT 1
                  FROM account_mapping filter_department
                 WHERE filter_department.user_id = {alias}.user_id
                   AND COALESCE(NULLIF(TRIM(filter_department.department), ''), '未维护部门') = ?
            )
            """
        )
        params.append(department)
    if owner:
        conditions.append(
            f"""
            EXISTS (
                SELECT 1
                  FROM account_mapping filter_owner
                 WHERE filter_owner.user_id = {alias}.user_id
                   AND COALESCE(NULLIF(TRIM(filter_owner.owner), ''), '未维护负责人') = ?
            )
            """
        )
        params.append(owner)
    return " AND ".join(conditions), params


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]
