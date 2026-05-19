from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings


class SettingsTest(unittest.TestCase):
    def test_auto_import_settings_are_read_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTO_IMPORT_ENABLED": "true",
                "DEEPSEEK_EXPORT_CURL_FILE": "/app/secrets/deepseek-export.curl",
                "DEEPSEEK_SINGLE_ACCOUNT_USER_ID": "deepseek-main",
                "AUTO_IMPORT_DAILY_TIME": "20:30",
                "AUTO_IMPORT_TIMEZONE": "Asia/Shanghai",
            },
            clear=False,
        ):
            settings = Settings()

        self.assertTrue(settings.auto_import_enabled)
        self.assertEqual(settings.deepseek_export_curl_file, Path("/app/secrets/deepseek-export.curl"))
        self.assertEqual(settings.deepseek_single_account_user_id, "deepseek-main")
        self.assertEqual(settings.auto_import_daily_time, "20:30")
        self.assertEqual(settings.auto_import_timezone, "Asia/Shanghai")

    def test_auto_import_timezone_defaults_to_process_timezone(self) -> None:
        with patch.dict(os.environ, {"TZ": "America/New_York"}, clear=True):
            settings = Settings()

        self.assertEqual(settings.auto_import_timezone, "America/New_York")

    def test_compose_sets_configurable_process_timezone(self) -> None:
        compose = Path(__file__).resolve().parents[1] / "docker-compose.yml"
        text = compose.read_text(encoding="utf-8")

        self.assertIn("TZ: ${TZ:-Asia/Shanghai}", text)
        self.assertIn("AUTO_IMPORT_TIMEZONE: ${AUTO_IMPORT_TIMEZONE:-${TZ:-Asia/Shanghai}}", text)


if __name__ == "__main__":
    unittest.main()
