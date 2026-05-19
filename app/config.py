from __future__ import annotations

import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.getenv("DATA_DIR", "data")).resolve()
        self.upload_retention_days = int(os.getenv("UPLOAD_RETENTION_DAYS", "365"))
        self.cleanup_enabled = os.getenv("CLEANUP_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
        self.app_password = os.getenv("APP_PASSWORD", "")
        self.auto_import_enabled = os.getenv("AUTO_IMPORT_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        self.deepseek_export_curl_file = Path(
            os.getenv("DEEPSEEK_EXPORT_CURL_FILE", "/app/secrets/deepseek-export.curl")
        )
        self.deepseek_single_account_user_id = os.getenv("DEEPSEEK_SINGLE_ACCOUNT_USER_ID", "").strip()
        self.auto_import_daily_time = os.getenv("AUTO_IMPORT_DAILY_TIME", "20:30")
        self.auto_import_timezone = os.getenv("AUTO_IMPORT_TIMEZONE", os.getenv("TZ", "Asia/Shanghai"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "deepseek_usage.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads" / "raw"

    @property
    def tmp_extract_dir(self) -> Path:
        return self.data_dir / "tmp" / "extract"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for path in (self.db_path.parent, self.upload_dir, self.tmp_extract_dir, self.export_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)
