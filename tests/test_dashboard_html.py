from __future__ import annotations

import unittest

from app.main import INDEX_HTML


class DashboardHtmlTest(unittest.TestCase):
    def test_dashboard_copy_and_sortable_tables_match_requested_layout(self) -> None:
        self.assertIn("每百万 Token 成本", INDEX_HTML)
        self.assertIn("API Key 用量排行", INDEX_HTML)
        self.assertIn("Top 20 API Key Token 分布", INDEX_HTML)
        self.assertIn("renderKeyModelRankChart(data.by_key_model", INDEX_HTML)
        self.assertIn("renderKeyTable(data.by_key || [])", INDEX_HTML)
        self.assertIn("renderTrendTable(data.trend || [])", INDEX_HTML)
        self.assertNotIn("renderKeyTable(data.by_key_model", INDEX_HTML)
        self.assertNotIn("renderTrendTable(data.trend_by_model", INDEX_HTML)
        self.assertNotIn("sortableTh(\"key\", \"model\", \"模型\")", INDEX_HTML)
        self.assertNotIn("sortableTh(\"trend\", \"model\", \"模型\")", INDEX_HTML)
        self.assertIn("sortableTh(\"key\"", INDEX_HTML)
        self.assertIn("sortableTh(\"trend\"", INDEX_HTML)
        self.assertIn("data-sort-table=\"${table}\"", INDEX_HTML)

        api_key_table_position = INDEX_HTML.index("API Key 汇总")
        department_chart_position = INDEX_HTML.index("部门费用分布")
        account_summary_position = INDEX_HTML.index("账号汇总")

        self.assertLess(api_key_table_position, department_chart_position)
        self.assertLess(api_key_table_position, account_summary_position)

    def test_key_and_trend_tables_use_fixed_height_scroll_containers(self) -> None:
        self.assertIn('id="keyTableWrap" class="table-wrap fixed-height-table"', INDEX_HTML)
        self.assertIn('id="trendTableWrap" class="table-wrap fixed-height-table"', INDEX_HTML)
        self.assertIn(".fixed-height-table { height: 420px; overflow: auto; }", INDEX_HTML)
        self.assertIn(".fixed-height-table { height: 360px; }", INDEX_HTML)

    def test_tables_use_refined_grid_styling(self) -> None:
        self.assertIn("th, td { border-right: 1px solid var(--line); border-bottom: 1px solid #e8eee8; padding: 11px 12px;", INDEX_HTML)
        self.assertIn("th:last-child, td:last-child { border-right: 0; }", INDEX_HTML)
        self.assertIn("tbody tr:nth-child(even) { background: #fbfdfb; }", INDEX_HTML)
        self.assertIn("tbody tr:hover { background: #eef8f3; }", INDEX_HTML)
        self.assertIn(".table-wrap { overflow: auto; margin-top: 12px; border: 1px solid var(--line-strong); border-radius: var(--radius); background: #fff;", INDEX_HTML)
        self.assertIn(".table-wrap table { min-width: 100%; background: #fff; }", INDEX_HTML)
        self.assertIn("#modelTable th:first-child, #modelTable td:first-child { width: 22%; overflow-wrap: anywhere; }", INDEX_HTML)

    def test_single_account_mode_hides_multi_account_only_panels(self) -> None:
        self.assertIn('id="departmentCostPanel"', INDEX_HTML)
        self.assertIn('id="ownerCostPanel"', INDEX_HTML)
        self.assertIn('id="accountHeatmapPanel"', INDEX_HTML)
        self.assertIn('id="departmentSummaryPanel"', INDEX_HTML)
        self.assertIn('id="accountSummaryPanel"', INDEX_HTML)
        self.assertIn('id="modelSummaryPanel"', INDEX_HTML)
        self.assertIn('class="panel chart-panel span-6 multi-account-only hidden"', INDEX_HTML)
        self.assertIn('id="accountHeatmapPanel" class="panel chart-panel span-12 multi-account-only hidden"', INDEX_HTML)
        self.assertIn('class="panel multi-account-only hidden"', INDEX_HTML)
        self.assertIn('<div class="grid two summary-bottom-grid">', INDEX_HTML)
        self.assertIn("function applyAccountMode(accountMode)", INDEX_HTML)
        self.assertIn('document.querySelectorAll(".multi-account-only")', INDEX_HTML)
        self.assertIn('panel.classList.toggle("hidden", singleAccountMode)', INDEX_HTML)
        self.assertIn('if (singleAccountMode) disposeChart("departmentChart");', INDEX_HTML)
        self.assertIn('if (singleAccountMode) disposeChart("ownerChart");', INDEX_HTML)
        self.assertIn('if (singleAccountMode) disposeChart("heatmapChart");', INDEX_HTML)
        self.assertIn("applyAccountMode(data.account_mode)", INDEX_HTML)
        self.assertIn("renderAccountOptions(data.accounts)", INDEX_HTML)
        self.assertIn("renderKeyModelRankChart(data.by_key_model || [], data.by_key || [])", INDEX_HTML)

    def test_single_account_mode_hides_account_filters_and_mapping_tab(self) -> None:
        self.assertIn('id="accountFilterField" class="multi-account-only hidden"', INDEX_HTML)
        self.assertIn('id="departmentFilterField" class="multi-account-only hidden"', INDEX_HTML)
        self.assertIn('id="ownerFilterField" class="multi-account-only hidden"', INDEX_HTML)
        self.assertIn('id="accountsTab" class="multi-account-only hidden"', INDEX_HTML)
        self.assertIn('data-tab="accounts">账号映射</button>', INDEX_HTML)
        self.assertIn('if (singleAccountMode && $("accounts").classList.contains("active")) {', INDEX_HTML)
        self.assertIn('activateTab("dashboard", { load: false });', INDEX_HTML)

    def test_dashboard_defaults_to_this_month_and_has_quick_date_ranges(self) -> None:
        self.assertIn('data-range-preset="last-month">上月</button>', INDEX_HTML)
        self.assertIn('data-range-preset="this-month">本月</button>', INDEX_HTML)
        self.assertIn('data-range-preset="this-week">本周</button>', INDEX_HTML)
        self.assertIn('data-range-preset="yesterday">昨天</button>', INDEX_HTML)
        self.assertIn('data-range-preset="today">今天</button>', INDEX_HTML)
        self.assertIn("function dateRangeForPreset(preset, baseDate = new Date())", INDEX_HTML)
        self.assertIn('} else if (preset === "yesterday") {', INDEX_HTML)
        self.assertIn('setDateRange("this-month", { load: false });', INDEX_HTML)
        self.assertIn('document.querySelectorAll("[data-range-preset]")', INDEX_HTML)
        self.assertIn('button.classList.toggle("active", button.dataset.rangePreset === activeDatePreset)', INDEX_HTML)
        self.assertIn('setDateRange(event.currentTarget.dataset.rangePreset);', INDEX_HTML)
        self.assertIn('setDateRange("this-month");', INDEX_HTML)

    def test_dashboard_formats_total_tokens_and_shows_cache_hit_rate_on_token_charts(self) -> None:
        self.assertIn("function formatTokenCount(value)", INDEX_HTML)
        self.assertIn('$("kpiTokens").textContent = formatTokenCount(data.kpi.total_tokens || 0);', INDEX_HTML)
        self.assertIn("function cacheHitRate(hitTokens, missTokens)", INDEX_HTML)
        self.assertIn("`缓存命中率：${cacheHitRate(row.cache_hit_tokens, row.cache_miss_tokens)}`", INDEX_HTML)
        self.assertIn('formatter: params => `命中率 ${cacheHitRate(params.data.source.cache_hit_tokens, params.data.source.cache_miss_tokens)}`', INDEX_HTML)
        self.assertIn("grid: { left: compactMode ? 8 : 12, right: compactMode ? 12 : 86", INDEX_HTML)
        self.assertIn('position: "right"', INDEX_HTML)
        self.assertNotIn('position: "insideRight"', INDEX_HTML)
        self.assertIn('backgroundColor: "rgba(255,255,255,0.92)"', INDEX_HTML)
        self.assertIn("hideOverlap: true", INDEX_HTML)
        self.assertIn("const totals = tokenTotalsByModel(rows);", INDEX_HTML)
        self.assertIn("`缓存命中率：${cacheHitRate(total.hit, total.miss)}`", INDEX_HTML)
        self.assertIn("formatter: params => `命中率 ${cacheHitRate(params.data.source.hit, params.data.source.miss)}`", INDEX_HTML)

    def test_key_summary_table_shows_cache_hit_rate_per_api_key(self) -> None:
        self.assertIn('<th class="row-index">序号</th>', INDEX_HTML)
        self.assertIn("sorted.map((r, index) =>", INDEX_HTML)
        self.assertIn('<td class="num row-index">${index + 1}</td>', INDEX_HTML)
        self.assertIn("#keyTable th.row-index, #keyTable td.row-index { width: 56px; color: var(--muted); text-align: center; }", INDEX_HTML)
        self.assertIn("#keyTable th:nth-child(n+4)", INDEX_HTML)
        self.assertNotIn('sortableTh("key", "account_count", "账号数")', INDEX_HTML)
        self.assertNotIn('<td class="num row-index">${index + 1}</td><td>${escapeHtml(r.key_name)}</td><td>${escapeHtml(r.account_name)}</td><td class="num">${fmt.format(r.account_count || 0)}</td>', INDEX_HTML)
        self.assertIn('<td class="num row-index">${index + 1}</td><td>${escapeHtml(r.key_name)}</td><td>${escapeHtml(r.account_name)}</td><td class="num">${money.format(r.cost || 0)}</td>', INDEX_HTML)
        self.assertIn('sortableTh("key", "cache_hit_rate", "缓存命中率")', INDEX_HTML)
        self.assertIn("cache_hit_rate: cacheHitRatio(r.cache_hit_tokens, r.cache_miss_tokens)", INDEX_HTML)
        self.assertIn("${percentFmt.format(r.cache_hit_rate)}", INDEX_HTML)

    def test_classic_dashboard_does_not_render_key_top_bottom_ranking(self) -> None:
        self.assertNotIn("API Key Top / Bottom 排行", INDEX_HTML)
        self.assertNotIn('id="keyTopRank"', INDEX_HTML)
        self.assertNotIn('id="keyBottomRank"', INDEX_HTML)
        self.assertNotIn("renderKeyTopBottomRank(data.by_key || [])", INDEX_HTML)
        self.assertNotIn("function renderKeyTopBottomRank(rows)", INDEX_HTML)
        self.assertNotIn("function renderKeyRankList(", INDEX_HTML)

    def test_classic_dashboard_removes_model_share_and_tallens_token_mix(self) -> None:
        self.assertNotIn("<h2>模型占比</h2>", INDEX_HTML)
        self.assertNotIn('<div id="modelShareChart"></div>', INDEX_HTML)
        self.assertNotIn("renderModelShareChart(data.by_model);", INDEX_HTML)
        self.assertIn("#tokenMixChart.echart { height: 420px; }", INDEX_HTML)
        self.assertIn('<div class="panel chart-panel span-12">', INDEX_HTML)

    def test_department_and_model_summary_tables_are_bottom_pair(self) -> None:
        self.assertIn('<div class="grid two summary-bottom-grid">', INDEX_HTML)
        self.assertIn(".grid.two.summary-bottom-grid { grid-template-columns: minmax(260px, 0.75fr) minmax(0, 1.25fr); align-items: stretch; }", INDEX_HTML)
        self.assertIn(".summary-bottom-grid > .panel { height: 100%; }", INDEX_HTML)
        account_summary_position = INDEX_HTML.index('id="accountSummaryPanel"')
        bottom_grid_position = INDEX_HTML.index('class="grid two summary-bottom-grid"')
        department_summary_position = INDEX_HTML.index('id="departmentSummaryPanel"')
        model_summary_position = INDEX_HTML.index('id="modelSummaryPanel"')
        api_key_table_position = INDEX_HTML.index("API Key 汇总")
        self.assertLess(api_key_table_position, account_summary_position)
        self.assertLess(account_summary_position, bottom_grid_position)
        self.assertLess(bottom_grid_position, department_summary_position)
        self.assertLess(department_summary_position, model_summary_position)

    def test_account_heatmap_uses_full_chart_width(self) -> None:
        self.assertIn('id="accountHeatmapPanel" class="panel chart-panel span-12 multi-account-only hidden"', INDEX_HTML)
        self.assertNotIn('id="accountHeatmapPanel" class="panel chart-panel span-8 multi-account-only hidden"', INDEX_HTML)

    def test_account_mapping_csv_upload_uses_file_panel(self) -> None:
        self.assertIn('id="accountImportForm" class="upload-box mapping-upload-box"', INDEX_HTML)
        self.assertIn('<span class="file-badge">CSV</span>', INDEX_HTML)
        self.assertIn('<strong>选择账号映射 CSV</strong>', INDEX_HTML)
        self.assertIn('id="accountsCsvSummary">未选择文件</span>', INDEX_HTML)
        self.assertIn('id="accountsCsv" accept=".csv" required aria-describedby="accountsCsvSummary"', INDEX_HTML)
        self.assertIn('class="upload-actions"', INDEX_HTML)
        self.assertIn("function updateAccountsCsvSummary()", INDEX_HTML)
        self.assertIn('accountsCsv").addEventListener("change", updateAccountsCsvSummary)', INDEX_HTML)
        self.assertIn("updateAccountsCsvSummary();", INDEX_HTML)
        self.assertNotIn('id="accountImportForm" class="row" style="margin-top: 14px;"', INDEX_HTML)

    def test_trend_chart_shows_cache_hit_rate_series(self) -> None:
        self.assertIn("function cacheHitRatio(hitTokens, missTokens)", INDEX_HTML)
        self.assertIn('name: "缓存命中率"', INDEX_HTML)
        self.assertIn("yAxisIndex: 2", INDEX_HTML)
        self.assertIn('name: compactMode ? "" : "命中率"', INDEX_HTML)
        self.assertIn("axisLabel: { formatter: value => percentFmt.format(value)", INDEX_HTML)
        self.assertIn("data: rows.map(row => ({ value: cacheHitRatio(row.cache_hit_tokens, row.cache_miss_tokens)", INDEX_HTML)
        self.assertIn("`缓存命中率：${cacheHitRate(row.cache_hit_tokens, row.cache_miss_tokens)}`", INDEX_HTML)
        self.assertIn('item.seriesName !== "费用" && item.seriesName !== "缓存命中率"', INDEX_HTML)

    def test_import_record_upload_time_is_formatted_for_browser_timezone(self) -> None:
        self.assertIn("function formatDateTime(value)", INDEX_HTML)
        self.assertNotIn('timeZone: "Asia/Shanghai"', INDEX_HTML)
        self.assertIn('hour12: false', INDEX_HTML)
        self.assertIn("escapeHtml(formatDateTime(r.uploaded_at))", INDEX_HTML)
        self.assertNotIn("escapeHtml(r.uploaded_at)", INDEX_HTML)

    def test_import_records_table_is_scroll_limited_and_paginated(self) -> None:
        self.assertIn('id="importsTableWrap" class="table-wrap fixed-height-table imports-table-wrap"', INDEX_HTML)
        self.assertIn('id="importsPagination" class="pagination hidden"', INDEX_HTML)
        self.assertIn("const importPageSize = 20;", INDEX_HTML)
        self.assertIn("let importCurrentPage = 1;", INDEX_HTML)
        self.assertIn("let importTotalRows = 0;", INDEX_HTML)
        self.assertIn("async function loadImports(page = importCurrentPage)", INDEX_HTML)
        self.assertIn('const data = await api(`/api/imports?page=${importCurrentPage}&page_size=${importPageSize}`);', INDEX_HTML)
        self.assertIn("renderImportsTable(data.items || []);", INDEX_HTML)
        self.assertIn("function renderImportsPagination()", INDEX_HTML)
        self.assertIn('importsPrevBtn").disabled = importCurrentPage <= 1;', INDEX_HTML)
        self.assertIn('importsNextBtn").disabled = importCurrentPage >= totalPages;', INDEX_HTML)

    def test_header_shows_data_update_time_from_dashboard_payload(self) -> None:
        self.assertIn("数据更新时间：${formatDateTime(data.data_updated_at)}", INDEX_HTML)
        self.assertNotIn("刷新时间：${new Date().toLocaleString()}", INDEX_HTML)

    def test_header_has_manual_sync_button_that_calls_auto_import_run(self) -> None:
        self.assertIn('id="manualSyncBtn"', INDEX_HTML)
        self.assertIn(">立即同步</button>", INDEX_HTML)
        self.assertIn("async function runManualSync()", INDEX_HTML)
        self.assertIn('manualSyncBtn").addEventListener("click", runManualSync)', INDEX_HTML)
        self.assertIn('button.textContent = "同步中..."', INDEX_HTML)
        self.assertIn("button.disabled = true", INDEX_HTML)
        self.assertIn('api("/api/auto-import/run", { method: "POST" })', INDEX_HTML)
        self.assertIn('result.status === "DUPLICATE"', INDEX_HTML)
        self.assertIn("await loadDashboard()", INDEX_HTML)
        self.assertIn('setDashboardNotice(message, "ok")', INDEX_HTML)
        self.assertIn('setDashboardNotice(error.message, "error")', INDEX_HTML)
        self.assertIn("button.disabled = false", INDEX_HTML)

    def test_usage_upload_supports_multiple_files_and_reports_partial_failures(self) -> None:
        self.assertIn('id="usageZip" accept=".zip" multiple required', INDEX_HTML)
        self.assertIn('class="file-dropzone"', INDEX_HTML)
        self.assertIn('id="usageZipSummary"', INDEX_HTML)
        self.assertIn('id="uploadProgressPanel"', INDEX_HTML)
        self.assertIn('id="uploadProgressBar"', INDEX_HTML)
        self.assertIn('id="currentUploadProgress"', INDEX_HTML)
        self.assertIn("Array.from($(\"usageZip\").files)", INDEX_HTML)
        self.assertIn("async function uploadUsageFile(file, onProgress", INDEX_HTML)
        self.assertIn("new XMLHttpRequest()", INDEX_HTML)
        self.assertIn("xhr.upload.onprogress", INDEX_HTML)
        self.assertIn("function setUploadProgress", INDEX_HTML)
        self.assertIn("currentFilePercent", INDEX_HTML)
        self.assertIn("for (const file of files)", INDEX_HTML)
        self.assertIn("failed.push({ file: file.name, error: error.message })", INDEX_HTML)
        self.assertIn("formatUploadSummary(results, failed)", INDEX_HTML)
        self.assertIn("失败文件：", INDEX_HTML)
        self.assertNotIn("$(\"usageZip\").files[0]", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
