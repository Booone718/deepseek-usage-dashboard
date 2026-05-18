from __future__ import annotations

import csv
import hashlib
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from .auto_import import AutoImportScheduler, redact_sensitive_text, run_auto_import_once
from .config import Settings
from .parser import load_usage_data
from .repository import Repository, now_iso


settings = Settings()
settings.ensure_dirs()
repo = Repository(settings.db_path, settings.data_dir)
security = HTTPBasic(auto_error=False)
auto_import_scheduler: AutoImportScheduler | None = None

app = FastAPI(title="DeepSeek Usage Dashboard")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


def require_auth(credentials: Annotated[HTTPBasicCredentials | None, Depends(security)] = None) -> None:
    if not settings.app_password:
        return
    if credentials and credentials.password == settings.app_password:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Basic"},
    )


@app.on_event("startup")
def startup() -> None:
    global auto_import_scheduler
    settings.ensure_dirs()
    repo.init_db()
    if settings.cleanup_enabled:
        repo.cleanup_uploads(settings.upload_retention_days)
    if settings.auto_import_enabled and auto_import_scheduler is None:
        auto_import_scheduler = AutoImportScheduler(
            job=_run_auto_import_job,
            daily_time=settings.auto_import_daily_time,
            timezone_name=settings.auto_import_timezone,
        )
        auto_import_scheduler.start()


@app.on_event("shutdown")
def shutdown() -> None:
    if auto_import_scheduler:
        auto_import_scheduler.stop()


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def index() -> str:
    return INDEX_HTML


@app.post("/api/upload", dependencies=[Depends(require_auth)])
async def upload_usage(file: UploadFile = File(...)) -> JSONResponse:
    filename = Path(file.filename or "usage.zip").name
    if Path(filename).suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="只支持上传 DeepSeek 导出的 .zip 文件")

    batch_id = f"imp_{datetime.now(timezone.utc).astimezone().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    month_dir = datetime.now(timezone.utc).astimezone().strftime("%Y/%m")
    stored_name = f"{batch_id}_{filename}"
    relative_path = Path("uploads") / "raw" / month_dir / stored_name
    stored_path = settings.data_dir / relative_path
    stored_path.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    with stored_path.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            digest.update(chunk)
            handle.write(chunk)
    sha256 = digest.hexdigest()

    duplicate = repo.find_import_by_sha256(sha256)
    if duplicate:
        stored_path.unlink(missing_ok=True)
        return JSONResponse({"status": "DUPLICATE", "batch": duplicate})

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

    work_dir = settings.tmp_extract_dir / batch_id
    try:
        amount, cost, _source_files, warnings = load_usage_data(stored_path, work_dir)
        repo.save_import_data(batch_id, amount, cost, warnings)
        return JSONResponse({"status": "SUCCESS", "batch_id": batch_id, "warnings": warnings})
    except Exception as exc:
        repo.mark_import_failed(batch_id, str(exc))
        raise HTTPException(status_code=400, detail=f"解析失败：{exc}") from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.get("/api/dashboard", dependencies=[Depends(require_auth)])
def dashboard_data(
    date_from: str | None = None,
    date_to: str | None = None,
    user_id: str | None = None,
    model: str | None = None,
    key: str | None = None,
    department: str | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    return repo.dashboard_data(date_from, date_to, user_id, model, key, department, owner)


@app.get("/api/imports", dependencies=[Depends(require_auth)])
def list_imports() -> list[dict[str, Any]]:
    return repo.list_imports()


@app.get("/api/auto-import/status", dependencies=[Depends(require_auth)])
def auto_import_status() -> dict[str, Any]:
    if not settings.auto_import_enabled:
        return {
            "enabled": False,
            "daily_time": settings.auto_import_daily_time,
            "timezone": settings.auto_import_timezone,
        }
    if not auto_import_scheduler:
        return {"enabled": True, "last_status": "NOT_STARTED"}
    return auto_import_scheduler.status()


@app.post("/api/auto-import/run", dependencies=[Depends(require_auth)])
def run_auto_import() -> dict[str, Any]:
    if auto_import_scheduler:
        return auto_import_scheduler.run_once()
    try:
        return _run_auto_import_job()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=redact_sensitive_text(str(exc))) from exc


@app.delete("/api/imports/{batch_id}", dependencies=[Depends(require_auth)])
def delete_import(batch_id: str) -> dict[str, Any]:
    deleted = repo.delete_import(batch_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    return {"deleted": True}


@app.get("/api/accounts", dependencies=[Depends(require_auth)])
def list_accounts() -> list[dict[str, Any]]:
    return repo.list_accounts()


@app.put("/api/accounts/{user_id}", dependencies=[Depends(require_auth)])
async def update_account(user_id: str, request: Request) -> dict[str, Any]:
    data = await request.json()
    repo.update_account(user_id, data)
    return {"updated": True}


@app.post("/api/accounts/import", dependencies=[Depends(require_auth)])
async def import_accounts(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = Path(file.filename or "accounts.csv").name
    if Path(filename).suffix.lower() != ".csv":
        raise HTTPException(status_code=400, detail="账号映射只支持 CSV")
    target = settings.data_dir / "tmp" / f"accounts_{uuid.uuid4().hex[:8]}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            handle.write(chunk)
    try:
        count = repo.import_accounts_csv(target)
    finally:
        target.unlink(missing_ok=True)
    return {"imported": count}


@app.get("/api/accounts/export", dependencies=[Depends(require_auth)])
def export_accounts() -> StreamingResponse:
    rows = repo.list_accounts()

    def generate():
        import io

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["user_id", "account_name", "owner", "department", "remark", "enabled"])
        writer.writeheader()
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in writer.fieldnames})
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=account_mapping.csv"},
    )


@app.post("/api/cleanup", dependencies=[Depends(require_auth)])
def cleanup() -> dict[str, Any]:
    removed = repo.cleanup_uploads(settings.upload_retention_days)
    return {"removed_upload_files": removed}


def _run_auto_import_job() -> dict[str, Any]:
    return run_auto_import_once(
        repo=repo,
        data_dir=settings.data_dir,
        tmp_extract_dir=settings.tmp_extract_dir,
        curl_file=settings.deepseek_export_curl_file,
        default_user_id=settings.deepseek_single_account_user_id,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> Response:
    if exc.status_code == 401:
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code, headers=exc.headers)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DeepSeek 用量看板</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='12' fill='%2317201d'/%3E%3Ctext x='32' y='39' text-anchor='middle' font-family='Arial' font-size='18' font-weight='700' fill='%23f4c76b'%3EDS%3C/text%3E%3C/svg%3E" />
  <style>
    :root {
      --bg: #f3f7f5;
      --ink: #17201d;
      --text: #22302c;
      --muted: #697873;
      --quiet: #8b9893;
      --panel: #ffffff;
      --panel-soft: #f8faf7;
      --line: #dfe6df;
      --line-strong: #cbd7d0;
      --brand: #0e6b5c;
      --brand-dark: #0b3d36;
      --blue: #315f9f;
      --cyan: #16879a;
      --green: #168a5a;
      --red: #b04452;
      --amber: #b36b22;
      --aubergine: #6c5270;
      --slate: #4e5d67;
      --shadow: 0 16px 34px rgba(30, 43, 38, 0.08);
      --shadow-soft: 0 8px 22px rgba(30, 43, 38, 0.06);
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    html { background: var(--bg); }
    body {
      margin: 0;
      min-width: 320px;
      background:
        linear-gradient(180deg, #e6efeb 0, #f3f7f5 250px, #f3f7f5 100%),
        var(--bg);
      color: var(--text);
      font-family: "Aptos", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }
    .shell { max-width: 1520px; margin: 0 auto; padding: 28px 28px 40px; }
    header {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 20px;
      align-items: center;
      margin-bottom: 14px;
      padding: 22px 24px;
      background: var(--ink);
      color: #f7fbf8;
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .brand-block { display: flex; gap: 16px; align-items: center; min-width: 0; }
    .brand-mark {
      width: 46px;
      height: 46px;
      flex: 0 0 46px;
      display: grid;
      place-items: center;
      border: 1px solid rgba(255, 255, 255, 0.18);
      border-radius: 7px;
      background: #f4c76b;
      color: #17201d;
      font-weight: 900;
      font-size: 14px;
      letter-spacing: 0;
    }
    h1 { margin: 0; color: #fff; font-size: 31px; line-height: 1.12; letter-spacing: 0; }
    h2 { margin: 0; color: var(--ink); font-size: 16px; line-height: 1.35; }
    .eyebrow { color: #9ad8c8; font-weight: 800; font-size: 12px; margin-bottom: 5px; }
    .muted { color: var(--muted); }
    header .muted { color: #b8c9c2; }
    .header-actions { display: flex; flex-direction: column; align-items: flex-end; gap: 9px; }
    .tabs {
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      gap: 4px;
      margin: 0 0 16px;
      padding: 5px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.92);
      width: max-content;
      max-width: 100%;
      overflow: auto;
      box-shadow: var(--shadow-soft);
      backdrop-filter: blur(10px);
    }
    .tabs button {
      min-width: 92px;
      height: 34px;
      border-color: transparent;
      background: transparent;
      color: var(--muted);
      font-weight: 700;
    }
    .tabs button.active { background: var(--ink); border-color: var(--ink); color: #fff; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 17px;
      margin-bottom: 16px;
      min-width: 0;
      box-shadow: var(--shadow-soft);
    }
    .filters-panel { padding: 14px; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .grid.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
    .kpi {
      position: relative;
      min-height: 108px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 15px 15px 14px;
      background: linear-gradient(180deg, #ffffff 0%, #fbfcfa 100%);
      box-shadow: var(--shadow-soft);
    }
    .kpi::before {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: var(--brand);
    }
    .kpi:nth-child(2)::before { background: var(--green); }
    .kpi:nth-child(3)::before { background: var(--amber); }
    .kpi:nth-child(4)::before { background: var(--cyan); }
    .kpi:nth-child(5)::before { background: var(--aubergine); }
    .kpi:nth-child(6)::before { background: var(--red); }
    .kpi:nth-child(7)::before { background: var(--slate); }
    .kpi:nth-child(8)::before { background: var(--blue); }
    .kpi .label { color: var(--muted); font-size: 12px; font-weight: 800; margin-bottom: 10px; }
    .kpi .value { color: var(--ink); font-size: 25px; font-weight: 850; line-height: 1.12; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
    .kpi .hint { color: var(--quiet); font-size: 12px; margin-top: 7px; }
    .toolbar { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 10px; align-items: end; }
    .filter-actions { justify-content: flex-end; margin-top: 12px; }
    label { display: block; color: var(--muted); font-size: 12px; font-weight: 800; margin-bottom: 6px; }
    input, select, button {
      height: 38px;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 0 11px;
      font: inherit;
      min-width: 0;
      outline: none;
      transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease, transform 0.15s ease;
    }
    input:hover, select:hover { border-color: #aebfb5; }
    input:focus, select:focus, button:focus-visible {
      border-color: var(--brand);
      box-shadow: 0 0 0 3px rgba(14, 107, 92, 0.16);
    }
    input[type="file"] { padding: 7px 10px; height: auto; min-height: 38px; }
    input[type="checkbox"] {
      width: 18px;
      height: 18px;
      min-height: 0;
      accent-color: var(--brand);
    }
    button {
      cursor: pointer;
      font-weight: 800;
      white-space: nowrap;
      background: #f8faf7;
    }
    button:hover { background: #eef4ef; }
    button:active { transform: translateY(1px); }
    button.primary {
      background: var(--brand);
      border-color: var(--brand);
      color: #fff;
      box-shadow: 0 10px 20px rgba(14, 107, 92, 0.2);
    }
    button.primary:hover { background: #0b5f52; }
    button.ghost { background: #fff; }
    button.danger { color: var(--red); background: #fff8f8; border-color: #efc7cc; }
    table { width: 100%; border-collapse: separate; border-spacing: 0; }
    th, td { border-bottom: 1px solid #e8eee8; padding: 11px 10px; text-align: left; vertical-align: middle; }
    th {
      position: sticky;
      top: 0;
      z-index: 1;
      color: var(--muted);
      font-weight: 850;
      font-size: 12px;
      background: #f7faf6;
    }
    .sort-button {
      width: 100%;
      height: auto;
      min-height: 0;
      display: inline-flex;
      justify-content: flex-start;
      align-items: center;
      gap: 6px;
      border: 0;
      border-radius: 0;
      background: transparent;
      color: inherit;
      padding: 0;
      box-shadow: none;
      font: inherit;
      font-weight: inherit;
      text-align: inherit;
    }
    .sort-button:hover { background: transparent; }
    .sort-button:focus-visible {
      border-color: transparent;
      box-shadow: 0 0 0 3px rgba(14, 107, 92, 0.14);
    }
    .sort-indicator {
      min-width: 12px;
      color: var(--brand);
      text-align: center;
    }
    #keyTable th:nth-child(n+3) .sort-button,
    #trendTable th:nth-child(n+2) .sort-button { justify-content: flex-end; }
    tbody tr { transition: background 0.15s ease; }
    tbody tr:hover { background: #f3faf6; }
    td { color: #2d3a35; }
    td.num { text-align: right; font-variant-numeric: tabular-nums; }
    td input { width: 100%; min-width: 150px; }
    td input[type="checkbox"] { width: 18px; min-width: 0; }
    .table-wrap { overflow: auto; margin-top: 12px; border: 1px solid var(--line); border-radius: var(--radius); }
    .table-wrap table { min-width: 100%; }
    #accountTable, #modelTable, #departmentTable { table-layout: fixed; }
    #accountTable { min-width: 100%; }
    #modelTable { min-width: 980px; }
    #modelTable th:first-child, #modelTable td:first-child { width: 34%; }
    #accountTable td:nth-child(7) { text-align: right; }
    #departmentTable th:nth-child(n+2),
    #accountTable th:nth-child(n+4),
    #modelTable th:nth-child(n+2),
    #keyTable th:nth-child(n+3),
    #trendTable th:nth-child(n+2) { text-align: right; }
    #keyTable { min-width: 900px; }
    #trendTable { min-width: 820px; }
    #accountsTable { min-width: 1080px; }
    #importsTable { min-width: 980px; }
    .table-wrap table th:first-child { border-top-left-radius: 7px; }
    .table-wrap table th:last-child { border-top-right-radius: 7px; }
    .status { min-height: 24px; margin-top: 10px; color: var(--muted); }
    .status.ok { color: var(--green); }
    .status.error { color: var(--red); }
    .notice {
      border: 1px solid #bad9cf;
      background: #effaf6;
      color: var(--brand-dark);
      border-radius: var(--radius);
      padding: 11px 12px;
      margin-bottom: 16px;
      box-shadow: var(--shadow-soft);
    }
    .notice.ok { border-color: #bad9cf; background: #effaf6; color: var(--brand-dark); }
    .notice.error { border-color: #f0c2c8; background: #fff5f6; color: #9e2c3b; }
    .hidden { display: none; }
    .section { display: none; }
    .section.active { display: block; }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .row input { flex: 1 1 220px; }
    .small { font-size: 12px; }
    .bar-wrap { height: 9px; width: 100%; max-width: 104px; min-width: 0; margin-left: auto; background: #edf2ed; border-radius: 999px; overflow: hidden; }
    .bar { height: 9px; background: linear-gradient(90deg, var(--brand), var(--cyan)); }
    .chart-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 16px; }
    .chart-panel { min-height: 374px; }
    .span-8 { grid-column: span 8; }
    .span-6 { grid-column: span 6; }
    .span-4 { grid-column: span 4; }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin: -2px 0 13px;
      padding-bottom: 10px;
      border-bottom: 1px solid #edf1ed;
    }
    .panel-subtitle { color: var(--muted); font-size: 12px; margin-top: 4px; }
    .chart { width: 100%; height: 260px; display: block; }
    .echart { width: 100%; height: 286px; }
    .echart.tall { height: 316px; }
    #accountRankChart.echart { height: 500px; }
    .chart-action { cursor: pointer; transition: opacity 0.15s ease, filter 0.15s ease, transform 0.15s ease; }
    .chart-action:hover { filter: saturate(1.16) brightness(0.96); opacity: 0.92; }
    .rank-row.chart-action { border-radius: 8px; padding: 6px; margin: -6px; }
    .rank-row.chart-action:hover { background: #f0f7f5; }
    .legend { display: flex; flex-wrap: wrap; gap: 8px 12px; margin-top: 10px; color: var(--muted); font-size: 12px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .legend-dot { width: 10px; height: 10px; border-radius: 999px; display: inline-block; }
    .rank-list { display: grid; gap: 12px; }
    .rank-row { display: grid; gap: 6px; }
    .rank-meta { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
    .rank-label { font-weight: 800; overflow-wrap: anywhere; }
    .rank-value { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .rank-track { height: 9px; border-radius: 999px; background: #edf2ed; overflow: hidden; }
    .rank-fill { height: 9px; border-radius: 999px; background: var(--brand); }
    .donut-layout { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 18px; align-items: center; min-height: 242px; }
    .donut { width: 170px; height: 170px; border-radius: 50%; display: grid; place-items: center; margin: 0 auto; }
    .donut-hole { width: 104px; height: 104px; border-radius: 50%; background: #fff; display: grid; place-items: center; text-align: center; padding: 10px; border: 1px solid var(--line); }
    .donut-value { font-weight: 850; font-size: 16px; }
    .donut-label { color: var(--muted); font-size: 12px; margin-top: 2px; }
    .metric-list { display: grid; gap: 10px; }
    .metric-item { display: grid; gap: 5px; }
    .metric-top { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; }
    .heatmap { width: 100%; border-collapse: separate; border-spacing: 4px; table-layout: fixed; }
    .heatmap th, .heatmap td { border: 0; padding: 8px; text-align: center; background: transparent; }
    .heatmap th { font-size: 11px; color: var(--muted); }
    .heat-cell { border-radius: 6px; color: #0f172a; font-variant-numeric: tabular-nums; min-width: 72px; }
    .empty-state {
      display: grid;
      place-items: center;
      min-height: 220px;
      color: var(--muted);
      border: 1px dashed var(--line-strong);
      border-radius: var(--radius);
      background: var(--panel-soft);
      text-align: center;
      padding: 16px;
    }
    .upload-box {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      border: 1px dashed #93baae;
      background: #f5fbf8;
      border-radius: var(--radius);
      padding: 18px;
      margin-top: 14px;
    }
    .chart-tooltip {
      position: fixed;
      z-index: 20;
      max-width: 280px;
      pointer-events: none;
      background: rgba(23, 32, 29, 0.96);
      color: #fff;
      border-radius: 8px;
      padding: 9px 10px;
      font-size: 12px;
      line-height: 1.5;
      box-shadow: 0 16px 34px rgba(23, 32, 29, 0.22);
      transform: translate(12px, 12px);
    }
    .chart-tooltip strong { display: block; font-size: 13px; margin-bottom: 2px; }
    @media (max-width: 1120px) {
      .toolbar, .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .span-8, .span-6, .span-4 { grid-column: span 12; }
    }
    @media (max-width: 760px) {
      .shell { padding: 14px; }
      header { grid-template-columns: 1fr; padding: 18px; }
      .brand-block { align-items: flex-start; }
      .brand-mark { width: 40px; height: 40px; flex-basis: 40px; }
      h1 { font-size: 25px; }
      .header-actions { align-items: flex-start; }
      .tabs { width: 100%; }
      .tabs button { min-width: 86px; }
      .grid, .grid.two, .toolbar, .kpi-grid, .donut-layout, .upload-box { grid-template-columns: 1fr; }
      .chart-grid { grid-template-columns: 1fr; }
      .span-8, .span-6, .span-4 { grid-column: auto; }
      .filter-actions { justify-content: flex-start; }
      #accountTable { min-width: 680px; }
      #modelTable { min-width: 620px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="brand-block">
        <div class="brand-mark">DS</div>
        <div>
          <div class="eyebrow">DeepSeek Usage Analytics</div>
          <h1>DeepSeek 用量看板</h1>
          <div class="muted">上传官方导出的 ZIP，按账号、模型、Key、部门和日期分析用量。</div>
        </div>
      </div>
      <div class="header-actions">
        <button class="primary" id="uploadShortcutBtn" type="button">上传数据</button>
        <div class="muted small" id="lastRefresh"></div>
      </div>
    </header>

    <div class="tabs">
      <button class="active" data-tab="dashboard">看板</button>
      <button data-tab="upload">上传</button>
      <button data-tab="accounts">账号映射</button>
      <button data-tab="imports">导入记录</button>
    </div>

    <section id="dashboard" class="section active">
      <div class="notice hidden" id="dashboardNotice"></div>
      <div class="panel filters-panel">
        <div class="toolbar">
          <div><label>开始日期</label><input type="date" id="dateFrom" /></div>
          <div><label>结束日期</label><input type="date" id="dateTo" /></div>
          <div><label>账号</label><select id="accountFilter"><option value="">全部账号</option></select></div>
          <div><label>模型</label><select id="modelFilter"><option value="">全部模型</option></select></div>
          <div><label>API Key</label><input id="keyFilter" placeholder="名称或掩码" /></div>
          <div><label>部门</label><select id="departmentFilter"><option value="">全部部门</option></select></div>
          <div><label>负责人</label><select id="ownerFilter"><option value="">全部负责人</option></select></div>
        </div>
        <div class="row filter-actions">
          <button class="primary" id="refreshBtn">刷新</button>
          <button id="resetBtn">重置</button>
        </div>
      </div>

      <div class="kpi-grid">
        <div class="kpi"><div class="label">总费用</div><div class="value" id="kpiCost">0</div></div>
        <div class="kpi"><div class="label">请求数</div><div class="value" id="kpiRequests">0</div></div>
        <div class="kpi"><div class="label">Token</div><div class="value" id="kpiTokens">0</div></div>
        <div class="kpi"><div class="label">活跃账号</div><div class="value" id="kpiAccounts">0</div></div>
        <div class="kpi"><div class="label">有用量 Key 数</div><div class="value" id="kpiKeys">0</div></div>
        <div class="kpi"><div class="label">模型数</div><div class="value" id="kpiModels">0</div></div>
        <div class="kpi"><div class="label">输出 Token</div><div class="value" id="kpiOutputTokens">0</div><div class="hint" id="kpiOutputShare">0%</div></div>
        <div class="kpi"><div class="label">每百万 Token 成本</div><div class="value" id="kpiAvgCost">0</div></div>
      </div>

      <div class="chart-grid">
        <div class="panel chart-panel span-8">
          <div class="panel-head">
            <div>
              <h2>费用与 Token 趋势</h2>
              <div class="panel-subtitle">按日期观察费用，并拆分不同模型的 Token 变化</div>
            </div>
          </div>
          <div id="trendChart"></div>
        </div>
        <div class="panel chart-panel span-4">
          <div class="panel-head">
            <div>
              <h2>模型占比</h2>
              <div class="panel-subtitle">按模型展示输入命中缓存、输入未命中缓存和输出</div>
            </div>
          </div>
          <div id="modelShareChart"></div>
        </div>
        <div class="panel chart-panel span-6">
          <div class="panel-head">
            <div>
              <h2>API Key 用量排行</h2>
              <div class="panel-subtitle">Top 20 API Key Token 分布，按模型堆叠</div>
            </div>
          </div>
          <div id="accountRankChart"></div>
        </div>
        <div class="panel chart-panel span-6">
          <div class="panel-head">
            <div>
              <h2>Token 类型结构</h2>
              <div class="panel-subtitle">按模型拆分输入命中缓存、输入未命中缓存和输出</div>
            </div>
          </div>
          <div id="tokenMixChart"></div>
        </div>
      </div>

      <div class="panel">
        <h2>API Key 汇总</h2>
        <div class="table-wrap"><table id="keyTable"></table></div>
      </div>
      <div class="panel">
        <h2>日期趋势</h2>
        <div class="table-wrap"><table id="trendTable"></table></div>
      </div>

      <div class="chart-grid">
        <div id="departmentCostPanel" class="panel chart-panel span-6 multi-account-only">
          <div class="panel-head">
            <div>
              <h2>部门费用分布</h2>
              <div class="panel-subtitle">账号映射维护部门后自动归集</div>
            </div>
          </div>
          <div id="departmentChart"></div>
        </div>
        <div id="ownerCostPanel" class="panel chart-panel span-6 multi-account-only">
          <div class="panel-head">
            <div>
              <h2>负责人费用分布</h2>
              <div class="panel-subtitle">账号映射维护负责人后自动归集</div>
            </div>
          </div>
          <div id="ownerChart"></div>
        </div>
        <div id="accountHeatmapPanel" class="panel chart-panel span-8 multi-account-only">
          <div class="panel-head">
            <div>
              <h2>账号-模型热力图</h2>
              <div class="panel-subtitle">快速识别账号在不同模型上的消耗集中度</div>
            </div>
          </div>
          <div id="heatmapChart"></div>
        </div>
        <div id="departmentSummaryPanel" class="panel chart-panel span-4 multi-account-only">
          <div class="panel-head">
            <div>
              <h2>部门汇总</h2>
              <div class="panel-subtitle">费用、账号数和 Token 汇总</div>
            </div>
          </div>
          <div class="table-wrap"><table id="departmentTable"></table></div>
        </div>
      </div>

      <div class="grid two">
        <div id="accountSummaryPanel" class="panel multi-account-only">
          <h2>账号汇总</h2>
          <div class="table-wrap"><table id="accountTable"></table></div>
        </div>
        <div id="modelSummaryPanel" class="panel multi-account-only">
          <h2>模型汇总</h2>
          <div class="table-wrap"><table id="modelTable"></table></div>
        </div>
      </div>
    </section>

    <section id="upload" class="section">
      <div class="panel">
        <h2>上传 DeepSeek 导出 ZIP</h2>
        <form id="uploadForm" class="upload-box">
          <input type="file" id="usageZip" accept=".zip" required />
          <button class="primary" type="submit">上传并解析</button>
        </form>
        <div class="status" id="uploadStatus"></div>
      </div>
    </section>

    <section id="accounts" class="section">
      <div class="panel">
        <h2>账号映射</h2>
        <form id="accountImportForm" class="row" style="margin-top: 14px;">
          <input type="file" id="accountsCsv" accept=".csv" />
          <button type="submit">导入 CSV</button>
          <button type="button" id="exportAccountsBtn">导出 CSV</button>
        </form>
        <div class="status" id="accountStatus"></div>
        <div class="table-wrap"><table id="accountsTable"></table></div>
      </div>
    </section>

    <section id="imports" class="section">
      <div class="panel">
        <div class="row" style="justify-content: space-between;">
          <h2>导入记录</h2>
          <button id="cleanupBtn">清理过期原始 ZIP</button>
        </div>
        <div class="status" id="importStatus"></div>
        <div class="table-wrap"><table id="importsTable"></table></div>
      </div>
    </section>
  </div>
  <div id="chartTooltip" class="chart-tooltip hidden"></div>

  <script>
    window.__APP_BASE__ = (() => {
      const path = window.location.pathname.replace(/\/$/, "");
      return path === "" || path === "/" ? "" : path;
    })();
    document.write(`<script src="${window.__APP_BASE__}/static/vendor/echarts.min.js"><\/script>`);
  </script>
  <script>
    const fmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
    const intFmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
    const compact = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 });
    const money = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 4 });
    const percentFmt = new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 1 });
    const palette = ["#0e6b5c", "#315f9f", "#b36b22", "#16879a", "#b04452", "#6c5270", "#168a5a", "#4e5d67"];
    const typeLabels = {
      input_cache_hit_tokens: "缓存命中输入",
      input_cache_miss_tokens: "缓存未命中输入",
      output_tokens: "输出 Token",
      request_count: "请求次数"
    };
    const tokenTypeSeries = [
      { key: "input_cache_hit_tokens", name: "输入命中缓存", color: "#168a5a" },
      { key: "input_cache_miss_tokens", name: "输入未命中缓存", color: "#315f9f" },
      { key: "output_tokens", name: "输出", color: "#b36b22" }
    ];
    const tableSortState = {
      key: { key: "tokens", direction: "desc" },
      trend: { key: "utc_date", direction: "asc" }
    };
    let dashboardData = null;
    const appBase = window.__APP_BASE__ ?? (() => {
      const path = window.location.pathname.replace(/\/$/, "");
      return path === "" || path === "/" ? "" : path;
    })();

    const $ = (id) => document.getElementById(id);
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, s => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[s]));
    const apiUrl = (path) => `${appBase}${path}`;
    const tipHtml = (title, lines = []) => `<strong>${escapeHtml(title)}</strong>${lines.map(line => `<div>${escapeHtml(line)}</div>`).join("")}`;
    const actionAttrs = (filters, tip) => `data-filter="${escapeHtml(JSON.stringify(filters || {}))}" data-tip="${escapeHtml(tip)}"`;
    const isCompactViewport = () => window.innerWidth <= 760;

    function activateTab(tab, options = {}) {
      document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
      document.querySelectorAll(".section").forEach(s => s.classList.toggle("active", s.id === tab));
      if (options.load === false) return;
      if (tab === "dashboard") loadDashboard().catch(error => setDashboardNotice(error.message, "error"));
      if (tab === "accounts") loadAccounts();
      if (tab === "imports") loadImports();
    }

    document.querySelectorAll(".tabs button").forEach(btn => {
      btn.addEventListener("click", () => activateTab(btn.dataset.tab));
    });

    function setStatus(id, text, kind = "") {
      const el = $(id);
      el.textContent = text;
      el.className = `status ${kind}`;
    }

    function setDashboardNotice(text, kind = "ok") {
      const el = $("dashboardNotice");
      if (!text) {
        el.className = "notice hidden";
        el.textContent = "";
        return;
      }
      el.textContent = text;
      el.className = `notice ${kind}`;
    }

    function moveTooltip(event) {
      const tooltip = $("chartTooltip");
      const margin = 16;
      const maxLeft = window.innerWidth - tooltip.offsetWidth - margin;
      const maxTop = window.innerHeight - tooltip.offsetHeight - margin;
      tooltip.style.left = `${Math.max(margin, Math.min(event.clientX, maxLeft))}px`;
      tooltip.style.top = `${Math.max(margin, Math.min(event.clientY, maxTop))}px`;
    }

    document.addEventListener("mouseover", (event) => {
      const target = event.target.closest("[data-tip]");
      if (!target) return;
      const tooltip = $("chartTooltip");
      tooltip.innerHTML = target.dataset.tip;
      tooltip.classList.remove("hidden");
      moveTooltip(event);
    });

    document.addEventListener("mousemove", (event) => {
      if (!$("chartTooltip").classList.contains("hidden")) moveTooltip(event);
    });

    document.addEventListener("mouseout", (event) => {
      if (event.target.closest("[data-tip]")) $("chartTooltip").classList.add("hidden");
    });

    document.addEventListener("click", (event) => {
      const target = event.target.closest("[data-filter]");
      if (!target) return;
      const filters = JSON.parse(target.dataset.filter || "{}");
      applyChartFilters(filters);
    });

    document.addEventListener("click", (event) => {
      const target = event.target.closest("[data-sort-table]");
      if (!target) return;
      const table = target.dataset.sortTable;
      const key = target.dataset.sortKey;
      const state = tableSortState[table];
      if (!state || !key) return;
      if (state.key === key) {
        state.direction = state.direction === "asc" ? "desc" : "asc";
      } else {
        state.key = key;
        state.direction = ["key_name", "utc_date", "account_name"].includes(key) ? "asc" : "desc";
      }
      if (table === "key") renderKeyTable(dashboardData?.by_key || []);
      if (table === "trend") renderTrendTable(dashboardData?.trend || []);
    });

    function applyChartFilters(filters) {
      const labels = [];
      if (filters.date) {
        $("dateFrom").value = filters.date;
        $("dateTo").value = filters.date;
        labels.push(`日期：${filters.date}`);
      }
      if (filters.user_id) {
        $("accountFilter").value = filters.user_id;
        labels.push(`账号：${filters.account_name || filters.user_id}`);
      }
      if (filters.model) {
        $("modelFilter").value = filters.model;
        labels.push(`模型：${filters.model}`);
      }
      if (filters.key) {
        $("keyFilter").value = filters.key;
        labels.push(`Key：${filters.key}`);
      }
      if (filters.department) {
        $("departmentFilter").value = filters.department;
        labels.push(`部门：${filters.department}`);
      }
      if (filters.owner) {
        $("ownerFilter").value = filters.owner;
        labels.push(`负责人：${filters.owner}`);
      }
      if (!labels.length) return;
      setDashboardNotice(`已应用图表筛选：${labels.join("，")}`, "ok");
      loadDashboard().catch(error => setDashboardNotice(error.message, "error"));
    }

    async function api(path, options) {
      const res = await fetch(apiUrl(path), options);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      return data;
    }

    function queryString() {
      const params = new URLSearchParams();
      if ($("dateFrom").value) params.set("date_from", $("dateFrom").value);
      if ($("dateTo").value) params.set("date_to", $("dateTo").value);
      if ($("accountFilter").value) params.set("user_id", $("accountFilter").value);
      if ($("modelFilter").value) params.set("model", $("modelFilter").value);
      if ($("keyFilter").value.trim()) params.set("key", $("keyFilter").value.trim());
      if ($("departmentFilter").value) params.set("department", $("departmentFilter").value);
      if ($("ownerFilter").value) params.set("owner", $("ownerFilter").value);
      return params.toString();
    }

    function applyAccountMode(accountMode) {
      const singleAccountMode = accountMode !== "multiple";
      document.querySelectorAll(".multi-account-only").forEach(panel => {
        panel.classList.toggle("hidden", singleAccountMode);
      });
      if (singleAccountMode) disposeChart("departmentChart");
      if (singleAccountMode) disposeChart("ownerChart");
      if (singleAccountMode) disposeChart("heatmapChart");
    }

    async function loadDashboard() {
      const data = await api(`/api/dashboard?${queryString()}`);
      dashboardData = data;
      applyAccountMode(data.account_mode);
      const totalTokens = Number(data.kpi.total_tokens || 0);
      const totalCost = Number(data.kpi.total_cost || 0);
      $("kpiCost").textContent = money.format(data.kpi.total_cost || 0);
      $("kpiRequests").textContent = fmt.format(data.kpi.total_requests || 0);
      $("kpiTokens").textContent = formatTokenCount(data.kpi.total_tokens || 0);
      $("kpiAccounts").textContent = fmt.format(data.kpi.account_count || 0);
      $("kpiKeys").textContent = fmt.format(data.kpi.key_count || 0);
      $("kpiModels").textContent = fmt.format(data.kpi.model_count || 0);
      $("kpiOutputTokens").textContent = compact.format(data.kpi.output_tokens || 0);
      $("kpiOutputShare").textContent = totalTokens ? `${percentFmt.format((data.kpi.output_tokens || 0) / totalTokens)} / 全部 Token` : "0% / 全部 Token";
      $("kpiAvgCost").textContent = totalTokens ? money.format(totalCost / totalTokens * 1000000) : money.format(0);
      renderAccountOptions(data.accounts);
      renderModelOptions(data.models);
      renderDepartmentOptions(data.departments || []);
      renderOwnerOptions(data.owners || []);
      renderTrendChart(data.trend, data.trend_by_model || []);
      renderModelShareChart(data.by_model);
      renderKeyModelRankChart(data.by_key_model || [], data.by_key || []);
      renderTokenMixChart(data.token_mix);
      if (data.account_mode === "multiple") {
        const departmentMetric = sumRows(data.by_department, "cost") > 0 ? "cost" : "tokens";
        const ownerMetric = sumRows(data.by_owner, "cost") > 0 ? "cost" : "tokens";
        renderRankChart(
          "departmentChart",
          data.by_department.slice(0, 8),
          "department",
          departmentMetric,
          (value) => departmentMetric === "cost" ? money.format(value) : `${compact.format(value)} Token`,
          (row) => `${fmt.format(row.account_count || 0)} 个账号`
        );
        renderRankChart(
          "ownerChart",
          data.by_owner.slice(0, 8),
          "owner",
          ownerMetric,
          (value) => ownerMetric === "cost" ? money.format(value) : `${compact.format(value)} Token`,
          (row) => `${compact.format(row.tokens || 0)} Token`
        );
        renderHeatmapChart(data.model_account);
      }
      renderDepartmentTable(data.by_department);
      renderAccountTable(data.by_account);
      renderModelTable(data.by_model);
      renderKeyTable(data.by_key || []);
      renderTrendTable(data.trend || []);
      $("lastRefresh").textContent = `刷新时间：${new Date().toLocaleString()}`;
    }

    function keepSelectValue(select, rows, valueKey, labelKey, emptyText) {
      const current = select.value;
      select.innerHTML = `<option value="">${emptyText}</option>` + rows.map(row =>
        `<option value="${escapeHtml(row[valueKey] ?? row)}">${escapeHtml(row[labelKey] ?? row)}</option>`
      ).join("");
      select.value = current;
    }

    function renderAccountOptions(accounts) {
      keepSelectValue($("accountFilter"), accounts.filter(a => a.enabled), "user_id", "account_name", "全部账号");
    }

    function renderModelOptions(models) {
      keepSelectValue($("modelFilter"), models, null, null, "全部模型");
    }

    function renderDepartmentOptions(departments) {
      keepSelectValue($("departmentFilter"), departments, null, null, "全部部门");
    }

    function renderOwnerOptions(owners) {
      keepSelectValue($("ownerFilter"), owners, null, null, "全部负责人");
    }

    function toNumber(value) {
      const number = Number(value || 0);
      return Number.isFinite(number) ? number : 0;
    }

    function formatTokenCount(value) {
      const tokens = toNumber(value);
      return `${compact.format(tokens)} Token`;
    }

    function cacheHitRate(hitTokens, missTokens) {
      return percentFmt.format(cacheHitRatio(hitTokens, missTokens));
    }

    function cacheHitRatio(hitTokens, missTokens) {
      const hit = toNumber(hitTokens);
      const miss = toNumber(missTokens);
      const inputTotal = hit + miss;
      return inputTotal ? hit / inputTotal : 0;
    }

    function tokenTotalsByModel(rows) {
      const totals = new Map();
      (rows || []).forEach(row => {
        if (!totals.has(row.model)) {
          totals.set(row.model, { tokens: 0, hit: 0, miss: 0 });
        }
        const total = totals.get(row.model);
        const amount = toNumber(row.amount);
        total.tokens += amount;
        if (row.type === "input_cache_hit_tokens") total.hit += amount;
        if (row.type === "input_cache_miss_tokens") total.miss += amount;
      });
      return totals;
    }

    function sumRows(rows, key) {
      return (rows || []).reduce((total, row) => total + toNumber(row[key]), 0);
    }

    function sortRows(rows, table) {
      const state = tableSortState[table];
      if (!state) return rows || [];
      const direction = state.direction === "asc" ? 1 : -1;
      return (rows || []).slice().sort((a, b) => compareSortValue(a[state.key], b[state.key]) * direction);
    }

    function compareSortValue(left, right) {
      const leftNumber = Number(left);
      const rightNumber = Number(right);
      if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
        return leftNumber - rightNumber;
      }
      return String(left ?? "").localeCompare(String(right ?? ""), "zh-CN", { numeric: true, sensitivity: "base" });
    }

    function sortableTh(table, key, label) {
      const state = tableSortState[table] || {};
      const indicator = state.key === key ? (state.direction === "asc" ? "↑" : "↓") : "";
      return `<th><button type="button" class="sort-button" data-sort-table="${table}" data-sort-key="${key}"><span>${label}</span><span class="sort-indicator">${indicator}</span></button></th>`;
    }

    function emptyBlock(text) {
      return `<div class="empty-state">${escapeHtml(text)}</div>`;
    }

    const charts = new Map();

    function disposeChart(id) {
      const chart = charts.get(id);
      if (chart) {
        chart.dispose();
        charts.delete(id);
      }
    }

    function chartElement(id, tall = false) {
      const el = $(id);
      if (!window.echarts) {
        el.innerHTML = emptyBlock("图表组件加载失败");
        return null;
      }
      el.classList.add("echart");
      el.classList.toggle("tall", tall);
      let chart = charts.get(id);
      if (!chart || chart.isDisposed?.()) {
        el.innerHTML = "";
        chart = echarts.init(el, null, { renderer: "canvas" });
        charts.set(id, chart);
      } else {
        chart.clear();
      }
      chart.off("click");
      return chart;
    }

    function renderEmptyChart(id, text) {
      disposeChart(id);
      const el = $(id);
      el.classList.remove("echart", "tall");
      el.innerHTML = emptyBlock(text);
    }

    function chartBaseOption() {
      return {
        color: palette,
        animationDuration: 650,
        animationEasing: "cubicOut",
        textStyle: {
          fontFamily: '"Aptos", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif',
          color: "#22302c"
        },
        tooltip: {
          trigger: "item",
          confine: true,
          backgroundColor: "rgba(23, 32, 29, 0.96)",
          borderWidth: 0,
          textStyle: { color: "#fff", fontSize: 12 },
          extraCssText: "box-shadow:0 16px 34px rgba(23,32,29,.22);border-radius:8px;"
        }
      };
    }

    function applyEchartClick(chart, fallbackFilter = null) {
      chart.on("click", (params) => {
        const filter = params.data?.filter || fallbackFilter?.(params);
        if (filter) applyChartFilters(filter);
      });
    }

    window.addEventListener("resize", () => {
      charts.forEach(chart => chart.resize());
    });

    function renderTrendChart(rows, modelRows) {
      rows = rows || [];
      if (!rows.length) {
        renderEmptyChart("trendChart", "暂无趋势数据");
        return;
      }
      const chart = chartElement("trendChart", true);
      if (!chart) return;
      const compactMode = isCompactViewport();
      const dates = rows.map(row => row.utc_date);
      const modelTotals = new Map();
      (modelRows || []).forEach(row => {
        modelTotals.set(row.model, (modelTotals.get(row.model) || 0) + toNumber(row.tokens));
      });
      const topModels = Array.from(modelTotals.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, compactMode ? 4 : 6)
        .map(item => item[0]);
      const modelByDate = new Map();
      (modelRows || []).forEach(row => {
        modelByDate.set(`${row.utc_date}|||${row.model}`, row);
      });
      const modelSeries = topModels.map((modelName, index) => ({
        name: modelName,
        type: "line",
        smooth: true,
        symbolSize: compactMode ? 5 : 7,
        lineStyle: { width: 2.5, color: palette[index % palette.length] },
        data: dates.map(date => {
          const row = modelByDate.get(`${date}|||${modelName}`);
          return { value: toNumber(row?.tokens), filter: { date, model: modelName }, source: row };
        }),
        emphasis: { focus: "series" }
      }));
      const tokenSeries = modelSeries.length ? modelSeries : [{
        name: "Token",
        type: "line",
        smooth: true,
        symbolSize: 8,
        lineStyle: { width: 3, color: "#0e6b5c" },
        areaStyle: { color: "rgba(14,107,92,.12)" },
        data: rows.map(row => ({ value: toNumber(row.tokens), filter: { date: row.utc_date } })),
        emphasis: { focus: "series" }
      }];
      const cacheRateSeries = {
        name: "缓存命中率",
        type: "line",
        smooth: true,
        yAxisIndex: 2,
        symbolSize: compactMode ? 5 : 7,
        lineStyle: { width: 2.4, type: "dashed", color: "#168a5a" },
        itemStyle: { color: "#168a5a" },
        data: rows.map(row => ({ value: cacheHitRatio(row.cache_hit_tokens, row.cache_miss_tokens), filter: { date: row.utc_date } })),
        emphasis: { focus: "series" }
      };
      chart.setOption({
        ...chartBaseOption(),
        tooltip: {
          ...chartBaseOption().tooltip,
          trigger: "axis",
          axisPointer: { type: "cross", label: { backgroundColor: "#17201d" } },
          formatter: (items) => {
            const row = rows[items[0].dataIndex];
            return [
              `<strong>${escapeHtml(row.utc_date)}</strong>`,
              `费用：${money.format(row.cost || 0)}`,
              `总 Token：${fmt.format(row.tokens || 0)}`,
              `请求数：${fmt.format(row.requests || 0)}`,
              `输入 Token：${fmt.format(row.input_tokens || 0)}`,
              `输出 Token：${fmt.format(row.output_tokens || 0)}`,
              `缓存命中率：${cacheHitRate(row.cache_hit_tokens, row.cache_miss_tokens)}`,
              ...items
                .filter(item => item.seriesName !== "费用" && item.seriesName !== "缓存命中率")
                .map(item => `${escapeHtml(item.seriesName)}：${formatTokenCount(item.value || 0)}`)
            ].join("<br>");
          }
        },
        legend: { type: "scroll", top: 0, right: 0, left: compactMode ? 0 : 120, itemWidth: 10, itemHeight: 10 },
        toolbox: {
          show: !compactMode,
          right: 0,
          top: 28,
          feature: {
            dataZoom: { yAxisIndex: "none" },
            restore: {},
            saveAsImage: { pixelRatio: 2 }
          }
        },
        grid: { left: compactMode ? 28 : 54, right: compactMode ? 58 : 104, top: compactMode ? 54 : 58, bottom: rows.length > 12 ? 64 : compactMode ? 44 : 38, containLabel: true },
        dataZoom: rows.length > 12 ? [{ type: "slider", height: 18, bottom: 20 }, { type: "inside" }] : [],
        xAxis: { type: "category", data: dates, boundaryGap: true, axisLabel: { color: "#697873", hideOverlap: true } },
        yAxis: [
          { type: "value", name: compactMode ? "" : "Token", axisLabel: { formatter: value => compact.format(value), color: "#697873" }, splitLine: { lineStyle: { color: "#e5ece5" } } },
          { type: "value", name: compactMode ? "" : "费用", axisLabel: { formatter: value => money.format(value), color: "#697873" }, splitLine: { show: false } },
          { type: "value", name: compactMode ? "" : "命中率", min: 0, max: 1, position: "right", offset: compactMode ? 36 : 52, axisLabel: { formatter: value => percentFmt.format(value), color: "#697873" }, splitLine: { show: false } }
        ],
        series: [
          {
            name: "费用",
            type: "bar",
            yAxisIndex: 1,
            barMaxWidth: 22,
            data: rows.map(row => ({ value: toNumber(row.cost), filter: { date: row.utc_date } })),
            itemStyle: { borderRadius: [4, 4, 0, 0], color: "#d6a13d" },
            emphasis: { focus: "series" }
          }
        ].concat(tokenSeries, [cacheRateSeries])
      }, true);
      applyEchartClick(chart);
    }

    function renderPieChart(id, rows, valueKey, labelKey, valueFormatter, filterFactory) {
      rows = (rows || []).filter(row => toNumber(row[valueKey]) > 0);
      if (!rows.length) {
        renderEmptyChart(id, "暂无占比数据");
        return;
      }
      const chart = chartElement(id);
      if (!chart) return;
      const compactMode = isCompactViewport();
      const sorted = rows.slice().sort((a, b) => toNumber(b[valueKey]) - toNumber(a[valueKey]));
      const top = sorted.slice(0, 8).map((row) => ({
        name: row[labelKey],
        value: toNumber(row[valueKey]),
        source: row,
        filter: filterFactory?.(row)
      }));
      const rest = sorted.slice(8).reduce((total, row) => total + toNumber(row[valueKey]), 0);
      if (rest > 0) {
        top.push({ name: "其他", value: rest, source: null });
      }
      chart.setOption({
        ...chartBaseOption(),
        legend: compactMode
          ? { type: "scroll", orient: "horizontal", left: 0, right: 0, bottom: 0, itemWidth: 10, itemHeight: 10 }
          : { type: "scroll", orient: "vertical", right: 0, top: 18, bottom: 18, width: 120 },
        series: [{
          type: "pie",
          radius: compactMode ? ["50%", "72%"] : ["45%", "72%"],
          center: compactMode ? ["50%", "45%"] : ["36%", "50%"],
          avoidLabelOverlap: true,
          minAngle: 4,
          label: { show: !compactMode, formatter: "{b}\n{d}%", color: "#22302c" },
          labelLine: { length: 10, length2: 6 },
          data: top,
          emphasis: { scale: true, scaleSize: 8, itemStyle: { shadowBlur: 14, shadowColor: "rgba(15,23,42,.18)" } }
        }],
        tooltip: {
          ...chartBaseOption().tooltip,
          formatter: params => `<strong>${escapeHtml(params.name)}</strong><br>${valueFormatter(params.value)}<br>${percentFmt.format(params.percent / 100)}`
        }
      }, true);
      applyEchartClick(chart);
    }

    function renderModelShareChart(rows) {
      rows = (rows || []).filter(row => toNumber(row.tokens) > 0);
      if (!rows.length) {
        renderEmptyChart("modelShareChart", "暂无模型数据");
        return;
      }
      const chart = chartElement("modelShareChart");
      if (!chart) return;
      const compactMode = isCompactViewport();
      const sorted = rows.slice().sort((a, b) => toNumber(a.tokens) - toNumber(b.tokens)).slice(-8);
      const models = sorted.map(row => row.model);
      chart.setOption({
        ...chartBaseOption(),
        legend: { type: "scroll", top: 0, left: 0, right: 0, itemWidth: 10, itemHeight: 10 },
        grid: { left: compactMode ? 84 : 104, right: 18, top: 42, bottom: 18, containLabel: true },
        xAxis: { type: "value", axisLabel: { formatter: value => compact.format(value), color: "#697873" }, splitLine: { lineStyle: { color: "#e5ece5" } } },
        yAxis: { type: "category", data: models, axisLabel: { color: "#22302c", width: compactMode ? 72 : 98, overflow: "truncate" } },
        series: tokenTypeSeries.map(item => ({
          name: item.name,
          type: "bar",
          stack: "tokens",
          barMaxWidth: 20,
          data: sorted.map(row => ({
            value: toNumber(row[item.key]),
            source: row,
            filter: { model: row.model }
          })),
          itemStyle: { color: item.color },
          label: item.key === "output_tokens" ? {
            show: !compactMode,
            position: "right",
            color: "#4e5d67",
            fontSize: 11,
            formatter: params => `命中率 ${cacheHitRate(params.data.source.cache_hit_tokens, params.data.source.cache_miss_tokens)}`
          } : { show: false },
          emphasis: { focus: "series" }
        })),
        tooltip: {
          ...chartBaseOption().tooltip,
          trigger: "axis",
          axisPointer: { type: "shadow" },
          formatter: params => {
            const row = params[0]?.data?.source || {};
            return [
              `<strong>${escapeHtml(row.model || "")}</strong>`,
              `总 Token：${formatTokenCount(row.tokens || 0)}`,
              `输入命中缓存：${formatTokenCount(row.cache_hit_tokens || 0)}`,
              `输入未命中缓存：${formatTokenCount(row.cache_miss_tokens || 0)}`,
              `缓存命中率：${cacheHitRate(row.cache_hit_tokens, row.cache_miss_tokens)}`,
              `输出：${formatTokenCount(row.output_tokens || 0)}`,
              `每百万 Token 成本：${money.format(row.cost_per_million_tokens || 0)}`
            ].join("<br>");
          }
        }
      }, true);
      applyEchartClick(chart, params => params.data?.filter);
    }

    function renderRankChart(id, rows, labelKey, valueKey, valueFormatter, subFormatter) {
      rows = (rows || []).filter(row => toNumber(row[valueKey]) > 0);
      if (!rows.length) {
        renderEmptyChart(id, "暂无排行数据");
        return;
      }
      const chart = chartElement(id);
      if (!chart) return;
      const sorted = rows.slice().sort((a, b) => toNumber(a[valueKey]) - toNumber(b[valueKey]));
      const labels = sorted.map(row => row[labelKey]);
      const data = sorted.map(row => {
        let filter = null;
        if (row.user_id) filter = { user_id: row.user_id, account_name: row.account_name };
        if (labelKey === "key_name") filter = { key: row.key_name };
        if (labelKey === "department") filter = { department: row.department };
        if (labelKey === "owner") filter = { owner: row.owner };
        return { value: toNumber(row[valueKey]), source: row, filter };
      });
      chart.setOption({
        ...chartBaseOption(),
        grid: { left: 12, right: 28, top: 18, bottom: 20, containLabel: true },
        xAxis: { type: "value", axisLabel: { formatter: value => valueKey === "cost" ? money.format(value) : compact.format(value), color: "#697873" }, splitLine: { lineStyle: { color: "#e5ece5" } } },
        yAxis: { type: "category", data: labels, axisLabel: { color: "#22302c", width: 118, overflow: "truncate" } },
        series: [{
          type: "bar",
          data,
          barMaxWidth: 18,
          itemStyle: {
            borderRadius: [0, 7, 7, 0],
            color: (params) => palette[params.dataIndex % palette.length]
          },
          label: {
            show: true,
            position: "right",
            color: "#4e5d67",
            formatter: params => valueFormatter(params.value)
          },
          emphasis: { focus: "self" }
        }],
        tooltip: {
          ...chartBaseOption().tooltip,
          formatter: params => {
            const row = params.data.source;
            return `<strong>${escapeHtml(params.name)}</strong><br>${valueFormatter(params.value)}<br>${escapeHtml(subFormatter(row))}`;
          }
        }
      }, true);
      applyEchartClick(chart);
    }

    function renderKeyModelRankChart(modelRows, keyRows) {
      modelRows = (modelRows || []).filter(row => toNumber(row.tokens) > 0);
      if (!modelRows.length) {
        renderEmptyChart("accountRankChart", "暂无 API Key 模型排行数据");
        return;
      }
      const keyTotals = new Map();
      modelRows.forEach(row => {
        if (!keyTotals.has(row.key_name)) {
          keyTotals.set(row.key_name, {
            key_name: row.key_name,
            tokens: 0,
            cost: 0,
            account_count: 0
          });
        }
        const total = keyTotals.get(row.key_name);
        total.tokens += toNumber(row.tokens);
        total.cost += toNumber(row.cost);
        total.account_count = Math.max(total.account_count, toNumber(row.account_count));
      });
      (keyRows || []).forEach(row => {
        keyTotals.set(row.key_name, {
          key_name: row.key_name,
          tokens: toNumber(row.tokens),
          cost: toNumber(row.cost),
          account_count: toNumber(row.account_count)
        });
      });
      const keys = Array.from(keyTotals.values())
        .sort((a, b) => b.tokens - a.tokens)
        .slice(0, 20)
        .reverse()
        .map(row => row.key_name);
      const keySet = new Set(keys);
      const models = Array.from(new Set(modelRows.filter(row => keySet.has(row.key_name)).map(row => row.model)))
        .sort((a, b) => {
          const totalA = sumRows(modelRows.filter(row => row.model === a && keySet.has(row.key_name)), "tokens");
          const totalB = sumRows(modelRows.filter(row => row.model === b && keySet.has(row.key_name)), "tokens");
          return totalB - totalA;
        });
      const values = new Map();
      modelRows.forEach(row => {
        values.set(`${row.key_name}|||${row.model}`, row);
      });
      const chart = chartElement("accountRankChart");
      if (!chart) return;
      chart.setOption({
        ...chartBaseOption(),
        legend: { type: "scroll", top: 0, left: 0, right: 0, itemWidth: 10, itemHeight: 10 },
        grid: { left: 106, right: 36, top: 42, bottom: 22, containLabel: true },
        xAxis: { type: "value", axisLabel: { formatter: value => compact.format(value), color: "#697873" }, splitLine: { lineStyle: { color: "#e5ece5" } } },
        yAxis: { type: "category", data: keys, axisLabel: { color: "#22302c", width: 98, overflow: "truncate" } },
        series: models.map((modelName, index) => ({
          name: modelName,
          type: "bar",
          stack: "tokens",
          barMaxWidth: 18,
          data: keys.map(keyName => {
            const row = values.get(`${keyName}|||${modelName}`);
            return {
              value: toNumber(row?.tokens),
              source: row,
              filter: { key: keyName, model: modelName }
            };
          }),
          itemStyle: {
            color: palette[index % palette.length],
            borderRadius: index === models.length - 1 ? [0, 7, 7, 0] : 0
          },
          emphasis: { focus: "series" }
        })),
        tooltip: {
          ...chartBaseOption().tooltip,
          trigger: "axis",
          axisPointer: { type: "shadow" },
          formatter: items => {
            const keyName = items[0]?.axisValue || "";
            const total = keyTotals.get(keyName) || {};
            const modelLines = items
              .filter(item => toNumber(item.value) > 0)
              .map(item => `${escapeHtml(item.seriesName)}：${fmt.format(item.value || 0)}`);
            return [
              `<strong>${escapeHtml(keyName)}</strong>`,
              `总 Token：${fmt.format(total.tokens || 0)}`,
              `费用：${money.format(total.cost || 0)}`,
              `账号数：${fmt.format(total.account_count || 0)}`,
              ...modelLines
            ].join("<br>");
          }
        }
      }, true);
      applyEchartClick(chart);
    }

    function renderTokenMixChart(rows) {
      rows = (rows || []).filter(row => row.type !== "request_count" && toNumber(row.amount) > 0);
      if (!rows.length) {
        renderEmptyChart("tokenMixChart", "暂无 Token 类型数据");
        return;
      }
      const chart = chartElement("tokenMixChart");
      if (!chart) return;
      const compactMode = isCompactViewport();
      const totals = tokenTotalsByModel(rows);
      const values = new Map();
      rows.forEach(row => {
        values.set(`${row.model}|||${row.type}`, toNumber(row.amount));
      });
      const models = Array.from(totals.entries())
        .sort((a, b) => b[1].tokens - a[1].tokens)
        .slice(0, compactMode ? 6 : 8)
        .map(item => item[0]);
      chart.setOption({
        ...chartBaseOption(),
        legend: { type: "scroll", top: 0, left: 0, right: 0, itemWidth: 10, itemHeight: 10 },
        grid: { left: 52, right: 18, top: 42, bottom: models.length > 4 ? 72 : 38, containLabel: true },
        xAxis: { type: "category", data: models, axisLabel: { color: "#22302c", interval: 0, rotate: models.length > 3 || compactMode ? 20 : 0, hideOverlap: true } },
        yAxis: { type: "value", axisLabel: { formatter: value => compact.format(value), color: "#697873" }, splitLine: { lineStyle: { color: "#e5ece5" } } },
        series: tokenTypeSeries.map(item => ({
          name: item.name,
          type: "bar",
          stack: "tokens",
          barMaxWidth: 34,
          data: models.map(modelName => ({
            value: values.get(`${modelName}|||${item.key}`) || 0,
            source: totals.get(modelName),
            filter: { model: modelName }
          })),
          itemStyle: { color: item.color },
          label: item.key === "output_tokens" ? {
            show: !compactMode,
            position: "top",
            color: "#4e5d67",
            fontSize: 11,
            formatter: params => `命中率 ${cacheHitRate(params.data.source.hit, params.data.source.miss)}`
          } : { show: false },
          emphasis: { focus: "series" }
        })),
        tooltip: {
          ...chartBaseOption().tooltip,
          trigger: "axis",
          axisPointer: { type: "shadow" },
          formatter: items => {
            const total = totals.get(items[0]?.axisValue || "") || { tokens: 0, hit: 0, miss: 0 };
            return [
              `<strong>${escapeHtml(items[0]?.axisValue || "")}</strong>`,
              `总 Token：${formatTokenCount(total.tokens)}`,
              `缓存命中率：${cacheHitRate(total.hit, total.miss)}`,
              ...items.map(item => `${escapeHtml(item.seriesName)}：${formatTokenCount(item.value || 0)}`)
            ].join("<br>");
          }
        }
      }, true);
      applyEchartClick(chart);
    }

    function renderHeatmapChart(rows) {
      rows = (rows || []).filter(row => toNumber(row.tokens) > 0);
      if (!rows.length) {
        renderEmptyChart("heatmapChart", "暂无账号-模型交叉数据");
        return;
      }
      const chart = chartElement("heatmapChart", true);
      if (!chart) return;
      const compactMode = isCompactViewport();
      const accountTotals = new Map();
      const modelTotals = new Map();
      const values = new Map();
      rows.forEach(row => {
        const account = row.account_name || "未知账号";
        const model = row.model || "未知模型";
        const tokens = toNumber(row.tokens);
        if (!accountTotals.has(account)) accountTotals.set(account, { tokens: 0, user_id: row.user_id, account_name: account });
        accountTotals.get(account).tokens += tokens;
        modelTotals.set(model, (modelTotals.get(model) || 0) + tokens);
        values.set(`${account}|||${model}`, { tokens, row });
      });
      const accounts = Array.from(accountTotals.entries()).sort((a, b) => b[1].tokens - a[1].tokens).slice(0, 8).map(item => item[0]);
      const models = Array.from(modelTotals.entries()).sort((a, b) => b[1] - a[1]).slice(0, 5).map(item => item[0]);
      const data = [];
      accounts.forEach((account, yIndex) => {
        models.forEach((model, xIndex) => {
          const item = values.get(`${account}|||${model}`);
          data.push({
            value: [xIndex, yIndex, item ? item.tokens : 0],
            filter: item ? { user_id: item.row.user_id, account_name: item.row.account_name, model: item.row.model } : null,
            source: item?.row || null
          });
        });
      });
      const max = Math.max(...data.map(item => item.value[2]), 1);
      chart.setOption({
        ...chartBaseOption(),
        grid: { left: compactMode ? 58 : 120, right: compactMode ? 10 : 24, top: 28, bottom: compactMode ? 86 : 74 },
        xAxis: { type: "category", data: models, axisLabel: { color: "#22302c", interval: 0, rotate: models.length > 3 || compactMode ? 20 : 0, margin: 14, hideOverlap: true } },
        yAxis: { type: "category", data: accounts, axisLabel: { color: "#22302c", width: compactMode ? 54 : 104, overflow: "truncate" } },
        visualMap: {
          show: false,
          min: 0,
          max,
          calculable: false,
          orient: "horizontal",
          left: "center",
          bottom: compactMode ? 16 : 8,
          itemWidth: compactMode ? 160 : 200,
          itemHeight: 10,
          textStyle: { color: "#697873" },
          inRange: { color: ["#edf5f1", "#9fd5c6", "#2c927c", "#0b3d36"] }
        },
        series: [{
          type: "heatmap",
          data,
          label: { show: true, color: "#17201d", formatter: params => params.value[2] ? compact.format(params.value[2]) : "" },
          emphasis: { itemStyle: { shadowBlur: 10, shadowColor: "rgba(15,23,42,.22)" } }
        }],
        tooltip: {
          ...chartBaseOption().tooltip,
          formatter: params => {
            const account = accounts[params.value[1]];
            const model = models[params.value[0]];
            const row = params.data.source;
            return `<strong>${escapeHtml(account)} / ${escapeHtml(model)}</strong><br>Token：${fmt.format(params.value[2] || 0)}<br>请求数：${fmt.format(row?.requests || 0)}<br>费用：${money.format(row?.cost || 0)}`;
          }
        }
      }, true);
      applyEchartClick(chart);
    }

    function renderDepartmentTable(rows) {
      $("departmentTable").innerHTML = `<thead><tr><th>部门</th><th>账号</th><th>费用</th><th>Token</th></tr></thead><tbody>` +
        (rows || []).slice(0, 8).map(r => `<tr><td>${escapeHtml(r.department)}</td><td class="num">${fmt.format(r.account_count || 0)}</td><td class="num">${money.format(r.cost || 0)}</td><td class="num">${compact.format(r.tokens || 0)}</td></tr>`).join("") +
        `</tbody>`;
    }

    function renderAccountTable(rows) {
      const max = Math.max(...rows.map(r => r.tokens || 0), 1);
      $("accountTable").innerHTML = `<thead><tr><th>账号</th><th>部门</th><th>负责人</th><th>费用</th><th>请求数</th><th>Token</th><th>占比</th></tr></thead><tbody>` +
        rows.map(r => `<tr><td>${escapeHtml(r.account_name)}</td><td>${escapeHtml(r.department)}</td><td>${escapeHtml(r.owner)}</td><td class="num">${money.format(r.cost || 0)}</td><td class="num">${fmt.format(r.requests || 0)}</td><td class="num">${fmt.format(r.tokens || 0)}</td><td><div class="bar-wrap"><div class="bar" style="width:${Math.max(2, (r.tokens || 0) / max * 100)}%"></div></div></td></tr>`).join("") +
        `</tbody>`;
    }

    function renderModelTable(rows) {
      $("modelTable").innerHTML = `<thead><tr><th>模型</th><th>费用</th><th>请求数</th><th>Token</th><th>命中缓存输入</th><th>未命中缓存输入</th><th>输出</th><th>每百万 Token</th></tr></thead><tbody>` +
        rows.map(r => `<tr><td>${escapeHtml(r.model)}</td><td class="num">${money.format(r.cost || 0)}</td><td class="num">${fmt.format(r.requests || 0)}</td><td class="num">${fmt.format(r.tokens || 0)}</td><td class="num">${fmt.format(r.cache_hit_tokens || 0)}</td><td class="num">${fmt.format(r.cache_miss_tokens || 0)}</td><td class="num">${fmt.format(r.output_tokens || 0)}</td><td class="num">${money.format(r.cost_per_million_tokens || 0)}</td></tr>`).join("") +
        `</tbody>`;
    }

    function renderKeyTable(rows) {
      const prepared = (rows || []).map(r => ({
        ...r,
        cost_per_million: r.tokens ? (r.cost || 0) / r.tokens * 1000000 : 0
      }));
      const sorted = sortRows(prepared, "key");
      $("keyTable").innerHTML = `<thead><tr>${sortableTh("key", "key_name", "API Key")}${sortableTh("key", "account_name", "关联账号")}${sortableTh("key", "account_count", "账号数")}${sortableTh("key", "cost", "费用")}${sortableTh("key", "requests", "请求数")}${sortableTh("key", "cache_hit_tokens", "命中缓存输入")}${sortableTh("key", "cache_miss_tokens", "未命中缓存输入")}${sortableTh("key", "output_tokens", "输出 Token")}${sortableTh("key", "tokens", "总 Token")}${sortableTh("key", "cost_per_million", "每百万 Token")}</tr></thead><tbody>` +
        sorted.map(r => `<tr><td>${escapeHtml(r.key_name)}</td><td>${escapeHtml(r.account_name)}</td><td class="num">${fmt.format(r.account_count || 0)}</td><td class="num">${money.format(r.cost || 0)}</td><td class="num">${fmt.format(r.requests || 0)}</td><td class="num">${fmt.format(r.cache_hit_tokens || 0)}</td><td class="num">${fmt.format(r.cache_miss_tokens || 0)}</td><td class="num">${fmt.format(r.output_tokens || 0)}</td><td class="num">${fmt.format(r.tokens || 0)}</td><td class="num">${money.format(r.cost_per_million)}</td></tr>`).join("") +
        `</tbody>`;
    }

    function renderTrendTable(rows) {
      const sorted = sortRows(rows, "trend");
      $("trendTable").innerHTML = `<thead><tr>${sortableTh("trend", "utc_date", "日期")}${sortableTh("trend", "cost", "费用")}${sortableTh("trend", "requests", "请求数")}${sortableTh("trend", "cache_hit_tokens", "命中缓存输入")}${sortableTh("trend", "cache_miss_tokens", "未命中缓存输入")}${sortableTh("trend", "output_tokens", "输出 Token")}${sortableTh("trend", "tokens", "总 Token")}</tr></thead><tbody>` +
        sorted.map(r => `<tr><td>${escapeHtml(r.utc_date)}</td><td class="num">${money.format(r.cost || 0)}</td><td class="num">${fmt.format(r.requests || 0)}</td><td class="num">${fmt.format(r.cache_hit_tokens || 0)}</td><td class="num">${fmt.format(r.cache_miss_tokens || 0)}</td><td class="num">${fmt.format(r.output_tokens || 0)}</td><td class="num">${fmt.format(r.tokens || 0)}</td></tr>`).join("") +
        `</tbody>`;
    }

    async function loadAccounts() {
      const rows = await api("/api/accounts");
      $("accountsTable").innerHTML = `<thead><tr><th>user_id</th><th>账号名称</th><th>负责人</th><th>部门</th><th>备注</th><th>启用</th><th>操作</th></tr></thead><tbody>` +
        rows.map(r => `<tr data-user="${escapeHtml(r.user_id)}">
          <td class="small">${escapeHtml(r.user_id)}</td>
          <td><input value="${escapeHtml(r.account_name)}" data-field="account_name"></td>
          <td><input value="${escapeHtml(r.owner)}" data-field="owner"></td>
          <td><input value="${escapeHtml(r.department)}" data-field="department"></td>
          <td><input value="${escapeHtml(r.remark)}" data-field="remark"></td>
          <td><input type="checkbox" ${r.enabled ? "checked" : ""} data-field="enabled"></td>
          <td><button onclick="saveAccount(this)">保存</button></td>
        </tr>`).join("") + `</tbody>`;
    }

    async function saveAccount(button) {
      const tr = button.closest("tr");
      const userId = tr.dataset.user;
      const data = {};
      tr.querySelectorAll("input").forEach(input => {
        data[input.dataset.field] = input.type === "checkbox" ? input.checked : input.value;
      });
      await api(`/api/accounts/${encodeURIComponent(userId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data)
      });
      setStatus("accountStatus", "已保存", "ok");
      loadDashboard();
    }

    async function loadImports() {
      const rows = await api("/api/imports");
      $("importsTable").innerHTML = `<thead><tr><th>批次</th><th>文件</th><th>状态</th><th>日期范围</th><th>行数</th><th>上传时间</th><th>错误</th><th>操作</th></tr></thead><tbody>` +
        rows.map(r => `<tr>
          <td class="small">${escapeHtml(r.id)}</td>
          <td>${escapeHtml(r.original_filename)}</td>
          <td>${escapeHtml(r.status)}</td>
          <td>${escapeHtml(r.min_utc_date || "")} 至 ${escapeHtml(r.max_utc_date || "")}</td>
          <td class="num">${fmt.format((r.amount_row_count || 0) + (r.cost_row_count || 0))}</td>
          <td class="small">${escapeHtml(r.uploaded_at)}</td>
          <td class="small">${escapeHtml(r.error_message || "")}</td>
          <td><button class="danger" onclick="deleteImport('${escapeHtml(r.id)}')">撤销</button></td>
        </tr>`).join("") + `</tbody>`;
    }

    async function deleteImport(batchId) {
      if (!confirm(`确认撤销导入批次 ${batchId}？`)) return;
      await api(`/api/imports/${encodeURIComponent(batchId)}`, { method: "DELETE" });
      setStatus("importStatus", "已撤销导入批次", "ok");
      loadImports();
      loadDashboard();
    }

    $("refreshBtn").addEventListener("click", loadDashboard);
    $("uploadShortcutBtn").addEventListener("click", () => activateTab("upload"));
    $("resetBtn").addEventListener("click", () => {
      ["dateFrom", "dateTo", "accountFilter", "modelFilter", "keyFilter", "departmentFilter", "ownerFilter"].forEach(id => $(id).value = "");
      setDashboardNotice("");
      loadDashboard();
    });

    $("uploadForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const file = $("usageZip").files[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      setStatus("uploadStatus", "正在上传并解析...");
      try {
        const result = await api("/api/upload", { method: "POST", body: form });
        const message = result.status === "DUPLICATE" ? "重复文件，已跳过；已切换到看板查看现有数据。" : "上传并解析完成；已切换到看板。";
        setStatus("uploadStatus", message, "ok");
        $("usageZip").value = "";
        await loadDashboard();
        activateTab("dashboard", { load: false });
        setDashboardNotice(message, "ok");
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch (error) {
        setStatus("uploadStatus", error.message, "error");
      }
    });

    $("accountImportForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const file = $("accountsCsv").files[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      const result = await api("/api/accounts/import", { method: "POST", body: form });
      setStatus("accountStatus", `已导入 ${result.imported} 条账号映射`, "ok");
      $("accountsCsv").value = "";
      loadAccounts();
      loadDashboard();
    });

    $("exportAccountsBtn").addEventListener("click", () => {
      window.location.href = apiUrl("/api/accounts/export");
    });

    $("cleanupBtn").addEventListener("click", async () => {
      const result = await api("/api/cleanup", { method: "POST" });
      setStatus("importStatus", `已清理 ${result.removed_upload_files} 个过期原始 ZIP`, "ok");
      loadImports();
    });

    loadDashboard().catch(error => console.error(error));
  </script>
</body>
</html>
"""
