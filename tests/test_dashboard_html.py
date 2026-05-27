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

    def test_single_account_mode_hides_multi_account_only_panels(self) -> None:
        self.assertIn('id="departmentCostPanel"', INDEX_HTML)
        self.assertIn('id="ownerCostPanel"', INDEX_HTML)
        self.assertIn('id="accountHeatmapPanel"', INDEX_HTML)
        self.assertIn('id="departmentSummaryPanel"', INDEX_HTML)
        self.assertIn('id="accountSummaryPanel"', INDEX_HTML)
        self.assertIn('id="modelSummaryPanel"', INDEX_HTML)
        self.assertIn('class="panel chart-panel span-6 multi-account-only hidden"', INDEX_HTML)
        self.assertIn('class="panel chart-panel span-8 multi-account-only hidden"', INDEX_HTML)
        self.assertIn('class="panel chart-panel span-4 multi-account-only hidden"', INDEX_HTML)
        self.assertIn('class="panel multi-account-only hidden"', INDEX_HTML)
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
        self.assertIn('sortableTh("key", "cache_hit_rate", "缓存命中率")', INDEX_HTML)
        self.assertIn("cache_hit_rate: cacheHitRatio(r.cache_hit_tokens, r.cache_miss_tokens)", INDEX_HTML)
        self.assertIn("${percentFmt.format(r.cache_hit_rate)}", INDEX_HTML)

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


if __name__ == "__main__":
    unittest.main()
