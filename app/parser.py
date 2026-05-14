from __future__ import annotations

import math
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

import pandas as pd


SUPPORTED_TABLE_EXTENSIONS = {".csv", ".xlsx", ".xlsm", ".xls"}
AMOUNT_REQUIRED = {"utc_date", "model", "type", "amount"}
COST_REQUIRED = {"utc_date", "model", "cost"}

TYPE_LABELS = {
    "input_cache_hit_tokens": "缓存命中输入",
    "input_cache_miss_tokens": "缓存未命中输入",
    "output_tokens": "输出 Token",
    "request_count": "请求次数",
}

COLUMN_ALIASES = {
    "utc date": "utc_date",
    "date": "utc_date",
    "日期": "utc_date",
    "模型": "model",
    "api key name": "api_key_name",
    "key name": "api_key_name",
    "api_key": "api_key",
    "api key": "api_key",
    "类型": "type",
    "指标": "type",
    "单价": "price",
    "数量": "amount",
    "用量": "amount",
    "花费": "cost",
    "金额": "cost",
    "币种": "currency",
    "钱包类型": "wallet_type",
}


def normalize_column_name(value: object) -> str:
    name = str(value).strip()
    lowered = " ".join(name.lower().replace("-", "_").split())
    lowered = lowered.replace(" ", "_")
    return COLUMN_ALIASES.get(name, COLUMN_ALIASES.get(lowered.replace("_", " "), lowered))


def safe_extract(zip_path: Path, destination: Path) -> list[Path]:
    destination = destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_name = member.filename.replace("\\", "/")
            if not member_name or member_name.endswith("/"):
                continue
            target = (destination / member_name).resolve()
            if destination not in target.parents and target != destination:
                raise ValueError(f"Unsafe zip member path: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(target)
    return extracted


def candidate_files(source: Path, work_dir: Path) -> list[tuple[Path, str, float, int]]:
    source = source.resolve()
    work_dir = work_dir.resolve()
    files: list[tuple[Path, str, float, int]] = []
    order = 0

    def add_file(path: Path, origin: str, mtime: float) -> None:
        nonlocal order
        if path.suffix.lower() in SUPPORTED_TABLE_EXTENSIONS:
            files.append((path, origin, mtime, order))
            order += 1

    if source.is_file():
        if source.suffix.lower() == ".zip":
            extracted = safe_extract(source, work_dir / source.stem)
            for path in extracted:
                add_file(path, f"{source.name}/{path.name}", source.stat().st_mtime)
        else:
            add_file(source, source.name, source.stat().st_mtime)
        return files

    if not source.exists():
        raise FileNotFoundError(f"Source path does not exist: {source}")

    for zip_path in sorted(source.glob("*.zip")):
        extracted = safe_extract(zip_path, work_dir / zip_path.stem)
        for path in extracted:
            add_file(path, f"{zip_path.name}/{path.name}", zip_path.stat().st_mtime)

    for path in sorted(source.glob("*")):
        if path.is_file():
            add_file(path, path.name, path.stat().st_mtime)

    return files


def read_csv_table(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def read_tables(path: Path) -> Iterable[tuple[str, pd.DataFrame]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        yield path.name, read_csv_table(path)
        return

    if suffix in {".xlsx", ".xlsm"}:
        sheets = pd.read_excel(path, sheet_name=None, dtype=str, keep_default_na=False, engine="openpyxl")
    elif suffix == ".xls":
        sheets = pd.read_excel(path, sheet_name=None, dtype=str, keep_default_na=False)
    else:
        return

    for sheet_name, frame in sheets.items():
        yield f"{path.name}::{sheet_name}", frame


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    clean = frame.copy()
    clean.columns = [normalize_column_name(c) for c in clean.columns]
    clean = clean.loc[:, [c for c in clean.columns if not str(c).startswith("unnamed")]]
    return clean.dropna(how="all")


def coerce_date(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d")


def coerce_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")


def optional_column(frame: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    if name in frame.columns:
        return frame[name].fillna("").astype(str).str.strip()
    return pd.Series([default] * len(frame), index=frame.index)


def normalize_amount(frame: pd.DataFrame, origin: str, mtime: float, order: int) -> pd.DataFrame:
    clean = normalize_frame(frame)
    missing = AMOUNT_REQUIRED - set(clean.columns)
    if missing:
        raise ValueError(f"not an amount table; missing {sorted(missing)}")

    amount = pd.DataFrame(index=clean.index)
    amount["user_id"] = optional_column(clean, "user_id")
    amount["utc_date"] = coerce_date(clean["utc_date"])
    amount["model"] = optional_column(clean, "model")
    amount["api_key_name"] = optional_column(clean, "api_key_name")
    amount["api_key"] = optional_column(clean, "api_key")
    amount["type"] = optional_column(clean, "type")
    amount["price"] = coerce_number(clean["price"]) if "price" in clean.columns else math.nan
    amount["amount"] = coerce_number(clean["amount"])
    amount["_source"] = origin
    amount["_source_mtime"] = mtime
    amount["_source_order"] = order
    amount = amount.dropna(subset=["utc_date", "amount"])
    return amount[amount["model"].ne("") & amount["type"].ne("")]


def normalize_cost(frame: pd.DataFrame, origin: str, mtime: float, order: int) -> pd.DataFrame:
    clean = normalize_frame(frame)
    missing = COST_REQUIRED - set(clean.columns)
    if missing:
        raise ValueError(f"not a cost table; missing {sorted(missing)}")

    cost = pd.DataFrame(index=clean.index)
    cost["user_id"] = optional_column(clean, "user_id")
    cost["utc_date"] = coerce_date(clean["utc_date"])
    cost["model"] = optional_column(clean, "model")
    cost["wallet_type"] = optional_column(clean, "wallet_type")
    cost["cost"] = coerce_number(clean["cost"])
    cost["currency"] = optional_column(clean, "currency", "CNY")
    cost["_source"] = origin
    cost["_source_mtime"] = mtime
    cost["_source_order"] = order
    cost = cost.dropna(subset=["utc_date", "cost"])
    return cost[cost["model"].ne("")]


def load_usage_data(source: Path, work_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    amount_frames: list[pd.DataFrame] = []
    cost_frames: list[pd.DataFrame] = []
    source_files: list[str] = []
    warnings: list[str] = []

    for path, origin, mtime, order in candidate_files(source, work_dir):
        source_files.append(origin)
        try:
            tables = list(read_tables(path))
        except Exception as exc:
            warnings.append(f"{origin}: 读取失败 - {exc}")
            continue

        for table_name, frame in tables:
            clean = normalize_frame(frame)
            columns = set(clean.columns)
            try:
                if AMOUNT_REQUIRED.issubset(columns):
                    amount_frames.append(normalize_amount(clean, f"{origin}::{table_name}", mtime, order))
                elif COST_REQUIRED.issubset(columns):
                    cost_frames.append(normalize_cost(clean, f"{origin}::{table_name}", mtime, order))
            except Exception as exc:
                warnings.append(f"{origin}::{table_name}: 解析失败 - {exc}")

    if not amount_frames and not cost_frames:
        raise ValueError("No amount or cost tables were found.")

    amount = pd.concat(amount_frames, ignore_index=True) if amount_frames else pd.DataFrame()
    cost = pd.concat(cost_frames, ignore_index=True) if cost_frames else pd.DataFrame()

    if not amount.empty:
        amount = amount.sort_values(["_source_mtime", "_source_order"])
        amount_keys = ["user_id", "utc_date", "model", "api_key_name", "api_key", "type"]
        amount = amount.drop_duplicates(subset=amount_keys, keep="last")

    if not cost.empty:
        cost = cost.sort_values(["_source_mtime", "_source_order"])
        cost_keys = ["user_id", "utc_date", "model", "wallet_type", "currency"]
        cost = cost.drop_duplicates(subset=cost_keys, keep="last")

    return amount, cost, sorted(set(source_files)), warnings
