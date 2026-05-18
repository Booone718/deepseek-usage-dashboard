# Account Mode Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add single-account and multi-account display modes to the DeepSeek usage dashboard without changing existing import or account mapping data.

**Architecture:** The repository layer will compute a global distinct non-empty `user_id` count from successful active usage rows and expose `account_mode` plus `global_account_count` in `/api/dashboard`. The browser will use that explicit mode to hide only account-comparison panels in single-account mode while preserving account filtering and mapping maintenance.

**Tech Stack:** Python 3.13, FastAPI, SQLite, unittest, inline HTML/CSS/JavaScript in `app/main.py`.

---

## File Structure

- Modify `app/repository.py`: compute `global_account_count` and `account_mode` inside `Repository.dashboard_data()`.
- Modify `app/main.py`: add stable ids/classes for account-comparison panels and a JavaScript mode toggle.
- Modify `tests/test_dashboard_repository.py`: add backend contract tests for global account mode.
- Modify `tests/test_dashboard_html.py`: add HTML/JavaScript structure tests for account mode display switching.

### Task 1: Backend Account Mode Contract

**Files:**
- Modify: `tests/test_dashboard_repository.py`
- Modify: `app/repository.py`

- [ ] **Step 1: Write failing repository tests**

Add these tests before the `if __name__ == "__main__":` block in `tests/test_dashboard_repository.py`:

```python
    def test_dashboard_marks_single_account_mode_from_global_usage(self) -> None:
        self.create_batch("batch1")
        self.repo.save_import_data(
            "batch1",
            amount_rows(
                [
                    {
                        "user_id": "user-a",
                        "utc_date": "2026-05-01",
                        "model": "deepseek-chat",
                        "api_key_name": "prod-key",
                        "api_key": "sk-prod",
                        "type": "input_cache_miss_tokens",
                        "price": 0.000001,
                        "amount": 100,
                        "_source": "amount.csv",
                    }
                ]
            ),
            empty_cost_rows(),
            [],
        )

        data = self.repo.dashboard_data()

        self.assertEqual(data["account_mode"], "single")
        self.assertEqual(data["global_account_count"], 1)

    def test_dashboard_account_mode_uses_global_accounts_not_current_filter(self) -> None:
        self.create_batch("batch1")
        self.repo.save_import_data(
            "batch1",
            amount_rows(
                [
                    {
                        "user_id": "user-a",
                        "utc_date": "2026-05-01",
                        "model": "deepseek-chat",
                        "api_key_name": "key-a",
                        "api_key": "sk-a",
                        "type": "input_cache_miss_tokens",
                        "price": 0.000001,
                        "amount": 100,
                        "_source": "amount.csv",
                    },
                    {
                        "user_id": "user-b",
                        "utc_date": "2026-05-02",
                        "model": "deepseek-chat",
                        "api_key_name": "key-b",
                        "api_key": "sk-b",
                        "type": "output_tokens",
                        "price": 0.000002,
                        "amount": 50,
                        "_source": "amount.csv",
                    },
                ]
            ),
            empty_cost_rows(),
            [],
        )

        data = self.repo.dashboard_data(user_id="user-a")

        self.assertEqual(data["kpi"]["account_count"], 1)
        self.assertEqual(data["account_mode"], "multiple")
        self.assertEqual(data["global_account_count"], 2)
```

- [ ] **Step 2: Run the focused repository tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dashboard_repository.DashboardRepositoryTest.test_dashboard_marks_single_account_mode_from_global_usage tests.test_dashboard_repository.DashboardRepositoryTest.test_dashboard_account_mode_uses_global_accounts_not_current_filter
```

Expected: both tests fail with missing `account_mode` or `global_account_count` keys.

- [ ] **Step 3: Implement backend contract**

In `app/repository.py`, inside `Repository.dashboard_data()` after `active_amount_cte = _active_amount_cte()` and inside the `with self.connect() as conn:` block, add:

```python
            global_account_count = conn.execute(
                f"""
                {active_amount_cte}
                SELECT COUNT(DISTINCT NULLIF(TRIM(user_id), '')) AS account_count
                  FROM active_amount
                """
            ).fetchone()["account_count"]
```

Then add these keys to the returned dictionary before `"kpi"`:

```python
            "account_mode": "single" if int(global_account_count or 0) <= 1 else "multiple",
            "global_account_count": int(global_account_count or 0),
```

- [ ] **Step 4: Run the focused repository tests and verify pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dashboard_repository.DashboardRepositoryTest.test_dashboard_marks_single_account_mode_from_global_usage tests.test_dashboard_repository.DashboardRepositoryTest.test_dashboard_account_mode_uses_global_accounts_not_current_filter
```

Expected: both tests pass.

### Task 2: Frontend Account Mode Display

**Files:**
- Modify: `tests/test_dashboard_html.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write failing HTML test**

Add this test to `tests/test_dashboard_html.py`:

```python
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
```

- [ ] **Step 2: Run the focused HTML test and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dashboard_html.DashboardHtmlTest.test_single_account_mode_hides_only_account_comparison_panels
```

Expected: fails because the ids, class names, and JavaScript toggle do not exist.

- [ ] **Step 3: Add DOM hooks and CSS-compatible classes**

In `app/main.py`, change the heatmap panel opening tag from:

```html
        <div class="panel chart-panel span-8">
```

to:

```html
        <div id="accountHeatmapPanel" class="panel chart-panel span-8 account-comparison-only">
```

Change the account summary panel opening tag from:

```html
        <div class="panel">
          <h2>账号汇总</h2>
```

to:

```html
        <div id="accountSummaryPanel" class="panel account-comparison-only">
          <h2>账号汇总</h2>
```

- [ ] **Step 4: Add JavaScript display toggle**

In `app/main.py`, add this function before `loadDashboard()`:

```javascript
    function applyAccountMode(accountMode) {
      const singleAccountMode = accountMode !== "multiple";
      document.querySelectorAll(".account-comparison-only").forEach(panel => {
        panel.classList.toggle("hidden", singleAccountMode);
      });
      if (singleAccountMode) disposeChart("heatmapChart");
    }
```

Inside `loadDashboard()`, after `dashboardData = data;`, add:

```javascript
      applyAccountMode(data.account_mode);
```

Change:

```javascript
      renderHeatmapChart(data.model_account);
```

to:

```javascript
      if (data.account_mode === "multiple") renderHeatmapChart(data.model_account);
```

Keep these existing calls unchanged:

```javascript
      renderAccountOptions(data.accounts);
      renderKeyModelRankChart(data.by_key_model || [], data.by_key || []);
```

- [ ] **Step 5: Run the focused HTML test and verify pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dashboard_html.DashboardHtmlTest.test_single_account_mode_hides_only_account_comparison_panels
```

Expected: pass.

### Task 3: Full Verification And Commit

**Files:**
- Verify: `app/repository.py`
- Verify: `app/main.py`
- Verify: `tests/test_dashboard_repository.py`
- Verify: `tests/test_dashboard_html.py`

- [ ] **Step 1: Run full test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 2: Review diff**

Run:

```powershell
git diff -- app/repository.py app/main.py tests/test_dashboard_repository.py tests/test_dashboard_html.py docs/superpowers/plans/2026-05-18-account-mode-display.md
```

Expected: diff only contains account mode contract, display toggle, tests, and this implementation plan.

- [ ] **Step 3: Commit implementation**

Run:

```powershell
git add -- app/repository.py app/main.py tests/test_dashboard_repository.py tests/test_dashboard_html.py docs/superpowers/plans/2026-05-18-account-mode-display.md
git commit -m "feat: 支持账号展示模式"
```

Expected: commit succeeds on `codex/account-mode-display`.
