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

from .config import Settings
from .parser import load_usage_data
from .repository import Repository, now_iso


settings = Settings()
settings.ensure_dirs()
repo = Repository(settings.db_path, settings.data_dir)
security = HTTPBasic(auto_error=False)

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
    settings.ensure_dirs()
    repo.init_db()
    if settings.cleanup_enabled:
        repo.cleanup_uploads(settings.upload_retention_days)


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
  <style>
    :root {
      --bg: #eef2f7;
      --panel: #ffffff;
      --panel-soft: #f8fafc;
      --text: #172033;
      --muted: #64748b;
      --line: #d5dde8;
      --blue: #2f6fed;
      --cyan: #0891b2;
      --green: #0f8f62;
      --red: #c2415b;
      --amber: #b7791f;
      --violet: #7c3aed;
      --slate: #475569;
      --shadow: 0 14px 36px rgba(15, 23, 42, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }
    .shell { max-width: 1480px; margin: 0 auto; padding: 24px; }
    header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 18px; }
    h1 { margin: 0; font-size: 30px; line-height: 1.15; letter-spacing: 0; }
    h2 { margin: 0; font-size: 17px; }
    .eyebrow { color: var(--blue); font-weight: 700; font-size: 12px; margin-bottom: 6px; }
    .muted { color: var(--muted); }
    .header-actions { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
    .tabs { display: flex; gap: 8px; margin-bottom: 16px; padding: 4px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, 0.72); width: max-content; max-width: 100%; overflow: auto; }
    .tabs button { min-width: 88px; border-color: transparent; background: transparent; }
    .tabs button.active { background: var(--text); border-color: var(--text); color: #fff; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 16px;
      min-width: 0;
      box-shadow: var(--shadow);
    }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .grid.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
    .kpi { border: 1px solid var(--line); border-top: 3px solid var(--blue); border-radius: 8px; padding: 14px; background: #fff; min-height: 96px; box-shadow: var(--shadow); }
    .kpi:nth-child(2) { border-top-color: var(--green); }
    .kpi:nth-child(3) { border-top-color: var(--amber); }
    .kpi:nth-child(4) { border-top-color: var(--cyan); }
    .kpi:nth-child(5) { border-top-color: var(--violet); }
    .kpi:nth-child(6) { border-top-color: var(--red); }
    .kpi:nth-child(7) { border-top-color: var(--slate); }
    .kpi:nth-child(8) { border-top-color: var(--blue); }
    .kpi .label { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
    .kpi .value { font-size: 24px; font-weight: 700; overflow-wrap: anywhere; }
    .kpi .hint { color: var(--muted); font-size: 12px; margin-top: 6px; }
    .toolbar { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 10px; align-items: end; }
    label { display: block; color: var(--muted); font-size: 12px; margin-bottom: 6px; }
    input, select, button {
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 0 10px;
      font: inherit;
      min-width: 0;
    }
    input[type="file"] { padding: 6px 10px; height: auto; }
    button { cursor: pointer; }
    button.primary { background: var(--blue); border-color: var(--blue); color: #fff; }
    button.ghost { background: #fff; }
    button.danger { color: var(--red); }
    table { width: 100%; border-collapse: collapse; }
    th, td { border-bottom: 1px solid #e8edf4; padding: 10px 8px; text-align: left; vertical-align: middle; }
    th { color: var(--muted); font-weight: 600; font-size: 12px; background: #fafbfe; }
    td.num { text-align: right; font-variant-numeric: tabular-nums; }
    .status { min-height: 24px; margin-top: 10px; color: var(--muted); }
    .status.ok { color: var(--green); }
    .status.error { color: var(--red); }
    .notice { border: 1px solid #bfdbfe; background: #eff6ff; color: #1d4ed8; border-radius: 8px; padding: 10px 12px; margin-bottom: 16px; }
    .notice.ok { border-color: #bbf7d0; background: #f0fdf4; color: #166534; }
    .notice.error { border-color: #fecdd3; background: #fff1f2; color: #be123c; }
    .hidden { display: none; }
    .section { display: none; }
    .section.active { display: block; }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .row input { flex: 1 1 180px; }
    .small { font-size: 12px; }
    .bar-wrap { height: 10px; background: #eef2f7; border-radius: 999px; overflow: hidden; min-width: 80px; }
    .bar { height: 10px; background: var(--blue); }
    .chart-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 16px; }
    .chart-panel { min-height: 360px; }
    .span-8 { grid-column: span 8; }
    .span-6 { grid-column: span 6; }
    .span-4 { grid-column: span 4; }
    .panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
    .panel-subtitle { color: var(--muted); font-size: 12px; margin-top: 4px; }
    .chart { width: 100%; height: 260px; display: block; }
    .echart { width: 100%; height: 278px; }
    .echart.tall { height: 306px; }
    .chart-action { cursor: pointer; transition: opacity 0.15s ease, filter 0.15s ease, transform 0.15s ease; }
    .chart-action:hover { filter: saturate(1.2) brightness(0.95); opacity: 0.9; }
    .rank-row.chart-action { border-radius: 8px; padding: 6px; margin: -6px; }
    .rank-row.chart-action:hover { background: #f1f6ff; }
    .legend { display: flex; flex-wrap: wrap; gap: 8px 12px; margin-top: 10px; color: var(--muted); font-size: 12px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .legend-dot { width: 10px; height: 10px; border-radius: 999px; display: inline-block; }
    .rank-list { display: grid; gap: 12px; }
    .rank-row { display: grid; gap: 6px; }
    .rank-meta { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
    .rank-label { font-weight: 600; overflow-wrap: anywhere; }
    .rank-value { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .rank-track { height: 10px; border-radius: 999px; background: #edf2f7; overflow: hidden; }
    .rank-fill { height: 10px; border-radius: 999px; background: var(--blue); }
    .donut-layout { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 18px; align-items: center; min-height: 242px; }
    .donut { width: 170px; height: 170px; border-radius: 50%; display: grid; place-items: center; margin: 0 auto; }
    .donut-hole { width: 104px; height: 104px; border-radius: 50%; background: #fff; display: grid; place-items: center; text-align: center; padding: 10px; border: 1px solid var(--line); }
    .donut-value { font-weight: 800; font-size: 16px; }
    .donut-label { color: var(--muted); font-size: 12px; margin-top: 2px; }
    .metric-list { display: grid; gap: 10px; }
    .metric-item { display: grid; gap: 5px; }
    .metric-top { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; }
    .heatmap { width: 100%; border-collapse: separate; border-spacing: 4px; table-layout: fixed; }
    .heatmap th, .heatmap td { border: 0; padding: 8px; text-align: center; background: transparent; }
    .heatmap th { font-size: 11px; color: var(--muted); }
    .heat-cell { border-radius: 6px; color: #0f172a; font-variant-numeric: tabular-nums; min-width: 72px; }
    .empty-state { display: grid; place-items: center; min-height: 220px; color: var(--muted); border: 1px dashed var(--line); border-radius: 8px; background: var(--panel-soft); text-align: center; padding: 16px; }
    .upload-box { border: 1px dashed #9db6dc; background: #f8fbff; border-radius: 8px; padding: 18px; margin-top: 14px; }
    .chart-tooltip {
      position: fixed;
      z-index: 20;
      max-width: 280px;
      pointer-events: none;
      background: rgba(15, 23, 42, 0.94);
      color: #fff;
      border-radius: 8px;
      padding: 9px 10px;
      font-size: 12px;
      line-height: 1.5;
      box-shadow: 0 16px 34px rgba(15, 23, 42, 0.22);
      transform: translate(12px, 12px);
    }
    .chart-tooltip strong { display: block; font-size: 13px; margin-bottom: 2px; }
    @media (max-width: 960px) {
      .grid, .grid.two, .toolbar, .kpi-grid, .donut-layout { grid-template-columns: 1fr; }
      .chart-grid { grid-template-columns: 1fr; }
      .span-8, .span-6, .span-4 { grid-column: auto; }
      header { display: block; }
      .header-actions { align-items: flex-start; margin-top: 12px; }
      .shell { padding: 14px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <div class="eyebrow">DeepSeek Usage Analytics</div>
        <h1>DeepSeek 用量看板</h1>
        <div class="muted">上传官方导出的 ZIP，按账号、模型、Key、部门和日期分析用量。</div>
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
      <div class="panel">
        <div class="toolbar">
          <div><label>开始日期</label><input type="date" id="dateFrom" /></div>
          <div><label>结束日期</label><input type="date" id="dateTo" /></div>
          <div><label>账号</label><select id="accountFilter"><option value="">全部账号</option></select></div>
          <div><label>模型</label><select id="modelFilter"><option value="">全部模型</option></select></div>
          <div><label>API Key</label><input id="keyFilter" placeholder="名称或掩码" /></div>
          <div><label>部门</label><select id="departmentFilter"><option value="">全部部门</option></select></div>
          <div><label>负责人</label><select id="ownerFilter"><option value="">全部负责人</option></select></div>
        </div>
        <div class="row" style="margin-top: 12px;">
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
        <div class="kpi"><div class="label">每千 Token 成本</div><div class="value" id="kpiAvgCost">0</div></div>
      </div>

      <div class="chart-grid">
        <div class="panel chart-panel span-8">
          <div class="panel-head">
            <div>
              <h2>费用与 Token 趋势</h2>
              <div class="panel-subtitle">按日期观察费用、Token 和请求量变化</div>
            </div>
          </div>
          <div id="trendChart"></div>
        </div>
        <div class="panel chart-panel span-4">
          <div class="panel-head">
            <div>
              <h2>模型占比</h2>
              <div class="panel-subtitle">优先按费用展示，费用为空时按 Token 展示</div>
            </div>
          </div>
          <div id="modelShareChart"></div>
        </div>
        <div class="panel chart-panel span-6">
          <div class="panel-head">
            <div>
              <h2>账号用量排行</h2>
              <div class="panel-subtitle">Top 8 账号 Token 分布</div>
            </div>
          </div>
          <div id="accountRankChart"></div>
        </div>
        <div class="panel chart-panel span-6">
          <div class="panel-head">
            <div>
              <h2>Token 类型结构</h2>
              <div class="panel-subtitle">输入、缓存命中、输出和请求次数构成</div>
            </div>
          </div>
          <div id="tokenMixChart"></div>
        </div>
        <div class="panel chart-panel span-6">
          <div class="panel-head">
            <div>
              <h2>部门费用分布</h2>
              <div class="panel-subtitle">账号映射维护部门后自动归集</div>
            </div>
          </div>
          <div id="departmentChart"></div>
        </div>
        <div class="panel chart-panel span-6">
          <div class="panel-head">
            <div>
              <h2>负责人费用分布</h2>
              <div class="panel-subtitle">账号映射维护负责人后自动归集</div>
            </div>
          </div>
          <div id="ownerChart"></div>
        </div>
        <div class="panel chart-panel span-8">
          <div class="panel-head">
            <div>
              <h2>账号-模型热力图</h2>
              <div class="panel-subtitle">快速识别账号在不同模型上的消耗集中度</div>
            </div>
          </div>
          <div id="heatmapChart"></div>
        </div>
        <div class="panel chart-panel span-4">
          <div class="panel-head">
            <div>
              <h2>部门汇总</h2>
              <div class="panel-subtitle">费用、账号数和 Token 汇总</div>
            </div>
          </div>
          <div style="overflow:auto;"><table id="departmentTable"></table></div>
        </div>
      </div>

      <div class="grid two">
        <div class="panel">
          <h2>账号汇总</h2>
          <div style="overflow:auto; margin-top: 12px;"><table id="accountTable"></table></div>
        </div>
        <div class="panel">
          <h2>模型汇总</h2>
          <div style="overflow:auto; margin-top: 12px;"><table id="modelTable"></table></div>
        </div>
      </div>
      <div class="panel">
        <h2>API Key 汇总</h2>
        <div style="overflow:auto; margin-top: 12px;"><table id="keyTable"></table></div>
      </div>
      <div class="panel">
        <h2>日期趋势</h2>
        <div style="overflow:auto; margin-top: 12px;"><table id="trendTable"></table></div>
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
        <div style="overflow:auto; margin-top: 12px;"><table id="accountsTable"></table></div>
      </div>
    </section>

    <section id="imports" class="section">
      <div class="panel">
        <div class="row" style="justify-content: space-between;">
          <h2>导入记录</h2>
          <button id="cleanupBtn">清理过期原始 ZIP</button>
        </div>
        <div class="status" id="importStatus"></div>
        <div style="overflow:auto; margin-top: 12px;"><table id="importsTable"></table></div>
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
    const palette = ["#2f6fed", "#0f8f62", "#b7791f", "#c2415b", "#7c3aed", "#0891b2", "#475569", "#db2777"];
    const typeLabels = {
      input_cache_hit_tokens: "缓存命中输入",
      input_cache_miss_tokens: "缓存未命中输入",
      output_tokens: "输出 Token",
      request_count: "请求次数"
    };
    const appBase = window.__APP_BASE__ ?? (() => {
      const path = window.location.pathname.replace(/\/$/, "");
      return path === "" || path === "/" ? "" : path;
    })();

    const $ = (id) => document.getElementById(id);
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, s => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[s]));
    const apiUrl = (path) => `${appBase}${path}`;
    const tipHtml = (title, lines = []) => `<strong>${escapeHtml(title)}</strong>${lines.map(line => `<div>${escapeHtml(line)}</div>`).join("")}`;
    const actionAttrs = (filters, tip) => `data-filter="${escapeHtml(JSON.stringify(filters || {}))}" data-tip="${escapeHtml(tip)}"`;

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

    async function loadDashboard() {
      const data = await api(`/api/dashboard?${queryString()}`);
      const totalTokens = Number(data.kpi.total_tokens || 0);
      const totalCost = Number(data.kpi.total_cost || 0);
      $("kpiCost").textContent = money.format(data.kpi.total_cost || 0);
      $("kpiRequests").textContent = fmt.format(data.kpi.total_requests || 0);
      $("kpiTokens").textContent = fmt.format(data.kpi.total_tokens || 0);
      $("kpiAccounts").textContent = fmt.format(data.kpi.account_count || 0);
      $("kpiKeys").textContent = fmt.format(data.kpi.key_count || 0);
      $("kpiModels").textContent = fmt.format(data.kpi.model_count || 0);
      $("kpiOutputTokens").textContent = compact.format(data.kpi.output_tokens || 0);
      $("kpiOutputShare").textContent = totalTokens ? `${percentFmt.format((data.kpi.output_tokens || 0) / totalTokens)} / 全部 Token` : "0% / 全部 Token";
      $("kpiAvgCost").textContent = totalTokens ? money.format(totalCost / totalTokens * 1000) : money.format(0);
      renderAccountOptions(data.accounts);
      renderModelOptions(data.models);
      renderDepartmentOptions(data.departments || []);
      renderOwnerOptions(data.owners || []);
      renderTrendChart(data.trend);
      renderModelShareChart(data.by_model);
      renderRankChart("accountRankChart", data.by_account.slice(0, 8), "account_name", "tokens", (value) => `${compact.format(value)} Token`, (row) => money.format(row.cost || 0));
      renderTokenMixChart(data.token_mix);
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
      renderDepartmentTable(data.by_department);
      renderAccountTable(data.by_account);
      renderModelTable(data.by_model);
      renderKeyTable(data.by_key);
      renderTrendTable(data.trend);
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

    function sumRows(rows, key) {
      return (rows || []).reduce((total, row) => total + toNumber(row[key]), 0);
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
          fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif',
          color: "#172033"
        },
        tooltip: {
          trigger: "item",
          confine: true,
          backgroundColor: "rgba(15, 23, 42, 0.94)",
          borderWidth: 0,
          textStyle: { color: "#fff", fontSize: 12 },
          extraCssText: "box-shadow:0 16px 34px rgba(15,23,42,.22);border-radius:8px;"
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

    function renderTrendChart(rows) {
      rows = rows || [];
      if (!rows.length) {
        renderEmptyChart("trendChart", "暂无趋势数据");
        return;
      }
      const chart = chartElement("trendChart", true);
      if (!chart) return;
      const dates = rows.map(row => row.utc_date);
      chart.setOption({
        ...chartBaseOption(),
        tooltip: {
          ...chartBaseOption().tooltip,
          trigger: "axis",
          axisPointer: { type: "cross", label: { backgroundColor: "#334155" } },
          formatter: (items) => {
            const row = rows[items[0].dataIndex];
            return [
              `<strong>${escapeHtml(row.utc_date)}</strong>`,
              `费用：${money.format(row.cost || 0)}`,
              `Token：${fmt.format(row.tokens || 0)}`,
              `请求数：${fmt.format(row.requests || 0)}`,
              `输入 Token：${fmt.format(row.input_tokens || 0)}`,
              `输出 Token：${fmt.format(row.output_tokens || 0)}`
            ].join("<br>");
          }
        },
        legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10 },
        toolbox: {
          right: 0,
          top: 28,
          feature: {
            dataZoom: { yAxisIndex: "none" },
            restore: {},
            saveAsImage: { pixelRatio: 2 }
          }
        },
        grid: { left: 54, right: 54, top: 58, bottom: rows.length > 12 ? 64 : 38, containLabel: true },
        dataZoom: rows.length > 12 ? [{ type: "slider", height: 18, bottom: 20 }, { type: "inside" }] : [],
        xAxis: { type: "category", data: dates, boundaryGap: true, axisLabel: { color: "#64748b" } },
        yAxis: [
          { type: "value", name: "Token / 请求", axisLabel: { formatter: value => compact.format(value), color: "#64748b" }, splitLine: { lineStyle: { color: "#e5ebf3" } } },
          { type: "value", name: "费用", axisLabel: { formatter: value => money.format(value), color: "#64748b" }, splitLine: { show: false } }
        ],
        series: [
          {
            name: "费用",
            type: "bar",
            yAxisIndex: 1,
            barMaxWidth: 22,
            data: rows.map(row => ({ value: toNumber(row.cost), filter: { date: row.utc_date } })),
            itemStyle: { borderRadius: [4, 4, 0, 0], color: "#f4c76b" },
            emphasis: { focus: "series" }
          },
          {
            name: "Token",
            type: "line",
            smooth: true,
            symbolSize: 8,
            areaStyle: { color: "rgba(47,111,237,.12)" },
            data: rows.map(row => ({ value: toNumber(row.tokens), filter: { date: row.utc_date } })),
            emphasis: { focus: "series" }
          },
          {
            name: "请求数",
            type: "line",
            smooth: true,
            symbolSize: 7,
            lineStyle: { type: "dashed" },
            data: rows.map(row => ({ value: toNumber(row.requests), filter: { date: row.utc_date } })),
            emphasis: { focus: "series" }
          }
        ]
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
        legend: { type: "scroll", orient: "vertical", right: 0, top: 18, bottom: 18, width: 120 },
        series: [{
          type: "pie",
          radius: ["45%", "72%"],
          center: ["36%", "50%"],
          avoidLabelOverlap: true,
          minAngle: 4,
          label: { formatter: "{b}\n{d}%", color: "#334155" },
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
      const costTotal = sumRows(rows, "cost");
      const valueKey = costTotal > 0 ? "cost" : "tokens";
      const formatter = valueKey === "cost" ? (value) => money.format(value) : (value) => `${compact.format(value)} Token`;
      renderPieChart(
        "modelShareChart",
        rows,
        valueKey,
        "model",
        formatter,
        row => ({ model: row.model })
      );
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
        if (labelKey === "department") filter = { department: row.department };
        if (labelKey === "owner") filter = { owner: row.owner };
        return { value: toNumber(row[valueKey]), source: row, filter };
      });
      chart.setOption({
        ...chartBaseOption(),
        grid: { left: 12, right: 28, top: 18, bottom: 20, containLabel: true },
        xAxis: { type: "value", axisLabel: { formatter: value => valueKey === "cost" ? money.format(value) : compact.format(value), color: "#64748b" }, splitLine: { lineStyle: { color: "#e5ebf3" } } },
        yAxis: { type: "category", data: labels, axisLabel: { color: "#334155", width: 118, overflow: "truncate" } },
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
            color: "#475569",
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

    function renderTokenMixChart(rows) {
      const tokenRows = (rows || [])
        .filter(row => row.type !== "request_count")
        .map(row => ({ label: typeLabels[row.type] || row.type, amount: row.amount }));
      renderPieChart(
        "tokenMixChart",
        tokenRows,
        "amount",
        "label",
        (value) => `${compact.format(value)} Token`,
        null
      );
    }

    function renderHeatmapChart(rows) {
      rows = (rows || []).filter(row => toNumber(row.tokens) > 0);
      if (!rows.length) {
        renderEmptyChart("heatmapChart", "暂无账号-模型交叉数据");
        return;
      }
      const chart = chartElement("heatmapChart", true);
      if (!chart) return;
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
        grid: { left: 120, right: 24, top: 28, bottom: 48 },
        xAxis: { type: "category", data: models, axisLabel: { color: "#334155", interval: 0, rotate: models.length > 3 ? 20 : 0 } },
        yAxis: { type: "category", data: accounts, axisLabel: { color: "#334155", width: 104, overflow: "truncate" } },
        visualMap: {
          min: 0,
          max,
          calculable: true,
          orient: "horizontal",
          left: "center",
          bottom: 0,
          inRange: { color: ["#eff6ff", "#93c5fd", "#2563eb", "#1e3a8a"] }
        },
        series: [{
          type: "heatmap",
          data,
          label: { show: true, color: "#0f172a", formatter: params => params.value[2] ? compact.format(params.value[2]) : "" },
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
      $("modelTable").innerHTML = `<thead><tr><th>模型</th><th>费用</th><th>请求数</th><th>Token</th><th>每千 Token</th></tr></thead><tbody>` +
        rows.map(r => `<tr><td>${escapeHtml(r.model)}</td><td class="num">${money.format(r.cost || 0)}</td><td class="num">${fmt.format(r.requests || 0)}</td><td class="num">${fmt.format(r.tokens || 0)}</td><td class="num">${r.tokens ? money.format((r.cost || 0) / r.tokens * 1000) : money.format(0)}</td></tr>`).join("") +
        `</tbody>`;
    }

    function renderKeyTable(rows) {
      $("keyTable").innerHTML = `<thead><tr><th>API Key</th><th>账号</th><th>费用</th><th>请求数</th><th>Token</th></tr></thead><tbody>` +
        rows.map(r => `<tr><td>${escapeHtml(r.key_name)}</td><td>${escapeHtml(r.account_name)}</td><td class="num">${money.format(r.cost || 0)}</td><td class="num">${fmt.format(r.requests || 0)}</td><td class="num">${fmt.format(r.tokens || 0)}</td></tr>`).join("") +
        `</tbody>`;
    }

    function renderTrendTable(rows) {
      $("trendTable").innerHTML = `<thead><tr><th>日期</th><th>费用</th><th>请求数</th><th>输入 Token</th><th>输出 Token</th><th>总 Token</th></tr></thead><tbody>` +
        rows.map(r => `<tr><td>${escapeHtml(r.utc_date)}</td><td class="num">${money.format(r.cost || 0)}</td><td class="num">${fmt.format(r.requests || 0)}</td><td class="num">${fmt.format(r.input_tokens || 0)}</td><td class="num">${fmt.format(r.output_tokens || 0)}</td><td class="num">${fmt.format(r.tokens || 0)}</td></tr>`).join("") +
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
