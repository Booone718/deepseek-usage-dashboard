from __future__ import annotations

import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.getenv("DATA_DIR", "data")).resolve()
        self.upload_retention_days = int(os.getenv("UPLOAD_RETENTION_DAYS", "365"))
        self.cleanup_enabled = os.getenv("CLEANUP_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
        self.app_password = os.getenv("APP_PASSWORD", "")

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
