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

    def test_single_account_mode_hides_only_account_comparison_panels(self) -> None:
        self.assertIn('id="accountHeatmapPanel"', INDEX_HTML)
        self.assertIn('id="accountSummaryPanel"', INDEX_HTML)
        self.assertIn('class="panel chart-panel span-8 account-comparison-only"', INDEX_HTML)
        self.assertIn('class="panel account-comparison-only"', INDEX_HTML)
        self.assertIn("function applyAccountMode(accountMode)", INDEX_HTML)
        self.assertIn('panel.classList.toggle("hidden", singleAccountMode)', INDEX_HTML)
        self.assertIn('if (singleAccountMode) disposeChart("heatmapChart");', INDEX_HTML)
        self.assertIn("applyAccountMode(data.account_mode)", INDEX_HTML)
        self.assertIn("renderAccountOptions(data.accounts)", INDEX_HTML)
        self.assertIn("renderKeyModelRankChart(data.by_key_model || [], data.by_key || [])", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
