from __future__ import annotations

import hashlib
import re
import shlex
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import pandas as pd

from .parser import load_usage_data
from .repository import Repository, now_iso


SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "x-csrf-token",
    "x-xsrf-token",
    "csrf-token",
    "x-api-key",
}
SENSITIVE_QUERY_NAMES = {
    "access_token",
    "api_key",
    "key",
    "signature",
    "token",
}


@dataclass(frozen=True)
class CurlRequest:
    url: str
    method: str = "GET"
    headers: dict[str, str] | None = None
    body: bytes | None = None

    def safe_summary(self) -> str:
        header_parts: list[str] = []
        for name, value in sorted((self.headers or {}).items()):
            if name.lower() in SENSITIVE_HEADER_NAMES:
                header_parts.append(f"{name}=<redacted>")
            else:
                header_parts.append(f"{name}={value}")
        body_part = " body=<present>" if self.body else ""
        return f"{self.method} {_redact_url(self.url)} headers=[{', '.join(header_parts)}]{body_part}"


class AutoImportScheduler:
    def __init__(self, *, job: Callable[[], dict[str, object]], daily_time: str, timezone_name: str) -> None:
        self.job = job
        self.daily_time = daily_time
        self.timezone_name = timezone_name
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_started_at = ""
        self._last_finished_at = ""
        self._last_status = "NEVER_RUN"
        self._last_error = ""
        self._last_result: dict[str, object] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="deepseek-auto-import", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def run_once(self) -> dict[str, object]:
        started_at = now_iso()
        with self._lock:
            self._last_started_at = started_at
            self._last_finished_at = ""
            self._last_status = "RUNNING"
            self._last_error = ""

        try:
            result = self.job()
            status = str(result.get("status", "SUCCESS"))
            with self._lock:
                self._last_status = status
                self._last_result = result
            return result
        except Exception as exc:
            error = redact_sensitive_text(str(exc))
            result = {"status": "FAILED", "error": error}
            with self._lock:
                self._last_status = "FAILED"
                self._last_error = error
                self._last_result = result
            return result
        finally:
            with self._lock:
                self._last_finished_at = now_iso()

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": True,
                "daily_time": self.daily_time,
                "timezone": self.timezone_name,
                "next_run_at": next_daily_run(datetime.now().astimezone(), self.daily_time, self.timezone_name).isoformat(),
                "last_started_at": self._last_started_at,
                "last_finished_at": self._last_finished_at,
                "last_status": self._last_status,
                "last_error": self._last_error,
                "last_result": self._last_result,
            }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            target = next_daily_run(datetime.now().astimezone(), self.daily_time, self.timezone_name)
            wait_seconds = max(0.0, (target - datetime.now(ZoneInfo(self.timezone_name))).total_seconds())
            if self._stop_event.wait(wait_seconds):
                break
            self.run_once()


def parse_curl_command(curl_command: str) -> CurlRequest:
    normalized = curl_command.replace("\\\r\n", " ").replace("\\\n", " ")
    normalized = normalized.replace("^\r\n", " ").replace("^\n", " ")
    tokens = shlex.split(normalized, posix=True)
    if not tokens or Path(tokens[0]).name.lower() not in {"curl", "curl.exe"}:
        raise ValueError("secret file must contain a curl command")

    url = ""
    method = ""
    headers: dict[str, str] = {}
    body: bytes | None = None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"-H", "--header"}:
            index += 1
            _add_header(headers, _required_value(tokens, index, token))
        elif token.startswith("-H") and token != "-H":
            _add_header(headers, token[2:])
        elif token.startswith("--header="):
            _add_header(headers, token.split("=", 1)[1])
        elif token in {"-X", "--request"}:
            index += 1
            method = _required_value(tokens, index, token).upper()
        elif token.startswith("--request="):
            method = token.split("=", 1)[1].upper()
        elif token in {"-b", "--cookie"}:
            index += 1
            headers["cookie"] = _required_value(tokens, index, token)
        elif token.startswith("--cookie="):
            headers["cookie"] = token.split("=", 1)[1]
        elif token in {"--data", "--data-raw", "--data-binary", "-d"}:
            index += 1
            body = _required_value(tokens, index, token).encode("utf-8")
        elif token.startswith("--data=") or token.startswith("--data-raw=") or token.startswith("--data-binary="):
            body = token.split("=", 1)[1].encode("utf-8")
        elif token in {"--compressed", "--location", "-L", "--insecure"}:
            pass
        elif token.startswith("-"):
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                index += 1
        elif token.startswith("http://") or token.startswith("https://"):
            url = token
        index += 1

    if not url:
        raise ValueError("curl command does not contain an export URL")
    if not url.startswith(("http://", "https://")):
        raise ValueError("export URL must be http or https")

    return CurlRequest(url=url, method=method or ("POST" if body else "GET"), headers=headers, body=body)


def download_export_from_curl(
    curl_command: str,
    target_dir: Path,
    *,
    urlopen_func: Callable[..., object] = urlopen,
) -> Path:
    export_request = parse_curl_command(curl_command)
    target_dir.mkdir(parents=True, exist_ok=True)

    request = Request(
        export_request.url,
        data=export_request.body,
        headers=export_request.headers or {},
        method=export_request.method,
    )
    response = urlopen_func(request, timeout=120)
    filename = _response_filename(response) or f"deepseek_usage_{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
    filename = Path(filename).name
    if Path(filename).suffix.lower() != ".zip":
        filename = f"{Path(filename).stem or 'deepseek_usage'}.zip"
    target_path = target_dir / filename
    with target_path.open("wb") as handle:
        handle.write(response.read())
    return target_path


def run_auto_import_once(
    *,
    repo: Repository,
    data_dir: Path,
    tmp_extract_dir: Path,
    curl_file: Path,
    default_user_id: str,
    export_timezone_name: str = "Asia/Shanghai",
    current_time: datetime | None = None,
    downloader: Callable[[str, Path], Path] = download_export_from_curl,
) -> dict[str, object]:
    if not default_user_id.strip():
        raise ValueError("DEEPSEEK_SINGLE_ACCOUNT_USER_ID is required for single-account auto import")
    if not curl_file.exists():
        raise ValueError(f"DeepSeek export curl secret file not found: {curl_file}")
    curl_command = curl_file.read_text(encoding="utf-8").strip()
    if not curl_command:
        raise ValueError("DeepSeek export curl secret is empty")
    download_dir = data_dir / "tmp" / "auto-import-downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    refreshed_curl_command = refresh_curl_export_month(
        curl_command,
        timezone_name=export_timezone_name,
        current_time=current_time,
    )
    archive_path = downloader(refreshed_curl_command, download_dir)
    return import_usage_archive(
        repo=repo,
        data_dir=data_dir,
        tmp_extract_dir=tmp_extract_dir,
        archive_path=archive_path,
        original_filename=archive_path.name,
        default_user_id=default_user_id,
        batch_prefix="auto",
    )


def refresh_curl_export_month(
    curl_command: str,
    *,
    timezone_name: str,
    current_time: datetime | None = None,
) -> str:
    export_request = parse_curl_command(curl_command)
    tz = ZoneInfo(timezone_name)
    local_now = (current_time or datetime.now(tz)).astimezone(tz)
    refreshed_url = _with_export_month(export_request.url, year=local_now.year, month=local_now.month)
    if refreshed_url == export_request.url:
        return curl_command
    return curl_command.replace(export_request.url, refreshed_url, 1)


def _with_export_month(url: str, *, year: int, month: int) -> str:
    parts = urlsplit(url)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    refreshed_pairs: list[tuple[str, str]] = []
    has_month = False
    has_year = False
    month_value_includes_year = False

    for name, value in query_pairs:
        lower_name = name.lower()
        if lower_name == "month":
            has_month = True
            month_value_includes_year = re.fullmatch(r"\d{4}-\d{1,2}", value.strip()) is not None
            refreshed_value = f"{year}-{month:02d}" if month_value_includes_year else str(month)
            refreshed_pairs.append((name, refreshed_value))
        elif lower_name == "year":
            has_year = True
            refreshed_pairs.append((name, str(year)))
        else:
            refreshed_pairs.append((name, value))

    if not has_month:
        refreshed_pairs.append(("month", str(month)))
    if not has_year and not month_value_includes_year:
        refreshed_pairs.append(("year", str(year)))

    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(refreshed_pairs), parts.fragment))


def next_daily_run(now: datetime, daily_time: str, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz)
    target_time = _parse_daily_time(daily_time)
    target = datetime.combine(local_now.date(), target_time, tzinfo=tz)
    if target <= local_now:
        target += timedelta(days=1)
    return target


def import_usage_archive(
    *,
    repo: Repository,
    data_dir: Path,
    tmp_extract_dir: Path,
    archive_path: Path,
    original_filename: str,
    default_user_id: str = "",
    batch_prefix: str = "imp",
) -> dict[str, object]:
    filename = Path(original_filename or archive_path.name).name
    if Path(filename).suffix.lower() != ".zip":
        raise ValueError("only DeepSeek .zip usage exports are supported")

    local_now = datetime.now().astimezone()
    batch_id = f"{batch_prefix}_{local_now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    month_dir = local_now.strftime("%Y/%m")
    relative_path = Path("uploads") / "raw" / month_dir / f"{batch_id}_{filename}"
    stored_path = data_dir / relative_path
    stored_path.parent.mkdir(parents=True, exist_ok=True)

    sha256 = _copy_with_sha256(archive_path, stored_path)
    duplicate = repo.find_import_by_sha256(sha256)
    if duplicate:
        stored_path.unlink(missing_ok=True)
        return {"status": "DUPLICATE", "batch": duplicate}

    repo.create_import_batch(
        {
            "id": batch_id,
            "original_filename": filename,
            "stored_path": relative_path.as_posix(),
            "sha256": sha256,
            "status": "PARSING",
            "uploaded_at": now_iso(),
        }
    )

    work_dir = tmp_extract_dir / batch_id
    try:
        amount, cost, _source_files, warnings = load_usage_data(stored_path, work_dir)
        _fill_default_user_id(amount, default_user_id)
        _fill_default_user_id(cost, default_user_id)
        repo.save_import_data(batch_id, amount, cost, warnings)
        return {"status": "SUCCESS", "batch_id": batch_id, "warnings": warnings}
    except Exception as exc:
        repo.mark_import_failed(batch_id, str(exc))
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _required_value(tokens: list[str], index: int, option: str) -> str:
    if index >= len(tokens):
        raise ValueError(f"{option} requires a value")
    return tokens[index]


def _add_header(headers: dict[str, str], header: str) -> None:
    if ":" not in header:
        raise ValueError(f"invalid header: {header}")
    name, value = header.split(":", 1)
    headers[name.strip().lower()] = value.strip()


def _copy_with_sha256(source: Path, target: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as src, target.open("wb") as dst:
        while chunk := src.read(1024 * 1024):
            digest.update(chunk)
            dst.write(chunk)
    return digest.hexdigest()


def _fill_default_user_id(frame: pd.DataFrame, default_user_id: str) -> None:
    user_id = default_user_id.strip()
    if not user_id or frame.empty:
        return
    if "user_id" not in frame.columns:
        frame["user_id"] = user_id
        return
    blank = frame["user_id"].fillna("").astype(str).str.strip().eq("")
    frame.loc[blank, "user_id"] = user_id


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(
        [(name, "<redacted>" if name.lower() in SENSITIVE_QUERY_NAMES else value) for name, value in parse_qsl(parts.query)]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def redact_sensitive_text(text: str) -> str:
    return re.sub(
        r"(?i)\b(cookie|authorization|x-csrf-token|x-xsrf-token|csrf-token|x-api-key)(\s*[:=]\s*)[^\s,]+",
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        text,
    )


def _parse_daily_time(value: str) -> time:
    try:
        hour, minute = value.split(":", 1)
        return time(int(hour), int(minute))
    except Exception as exc:
        raise ValueError("daily time must use HH:MM format") from exc


def _response_filename(response: object) -> str:
    headers = getattr(response, "headers", {})
    disposition = headers.get("Content-Disposition", "") if headers else ""
    for part in disposition.split(";"):
        part = part.strip()
        if part.lower().startswith("filename="):
            return part.split("=", 1)[1].strip().strip('"')
    url = getattr(response, "url", "")
    if url:
        return Path(urlsplit(url).path).name
    return ""
