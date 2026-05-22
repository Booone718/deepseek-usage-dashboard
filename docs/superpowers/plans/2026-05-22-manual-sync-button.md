# Manual Sync Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a top-right `立即同步` button that triggers the existing DeepSeek background export/import action from the dashboard page.

**Architecture:** Reuse the existing `POST /api/auto-import/run` backend endpoint. Add only browser-side controls and status handling in the inline dashboard HTML/JavaScript, plus HTML structure tests that verify the expected button, API call, loading state, and refresh behavior.

**Tech Stack:** Python 3, FastAPI, unittest, inline HTML/CSS/JavaScript in `app/main.py`.

---

## File Structure

- Modify `tests/test_dashboard_html.py`: add one HTML/JavaScript contract test for the manual sync button.
- Modify `app/main.py`: add the header button and the `runManualSync()` click handler.
- No backend files change because `POST /api/auto-import/run` already exists and is covered by existing auto-import tests.

### Task 1: Manual Sync Button HTML Contract

**Files:**
- Modify: `tests/test_dashboard_html.py`

- [ ] **Step 1: Write the failing HTML contract test**

Add this test method before the `if __name__ == "__main__":` block in `tests/test_dashboard_html.py`:

```python
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
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dashboard_html.DashboardHtmlTest.test_header_has_manual_sync_button_that_calls_auto_import_run
```

Expected: `FAIL` because `manualSyncBtn` and `runManualSync()` are not present yet.

### Task 2: Manual Sync Button Implementation

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Add the header button**

Change the header action block from:

```html
      <div class="header-actions">
        <button class="primary" id="uploadShortcutBtn" type="button">上传数据</button>
        <div class="muted small" id="lastRefresh"></div>
      </div>
```

to:

```html
      <div class="header-actions">
        <div class="row">
          <button class="primary" id="manualSyncBtn" type="button">立即同步</button>
          <button id="uploadShortcutBtn" type="button">上传数据</button>
        </div>
        <div class="muted small" id="lastRefresh"></div>
      </div>
```

- [ ] **Step 2: Add the manual sync JavaScript function**

Add this function near the upload form handler and before event bindings that use it:

```javascript
    async function runManualSync() {
      const button = $("manualSyncBtn");
      const originalText = button.textContent;
      button.disabled = true;
      button.textContent = "同步中...";
      setDashboardNotice("正在从 DeepSeek 后台同步数据...");
      try {
        const result = await api("/api/auto-import/run", { method: "POST" });
        const message = result.status === "DUPLICATE"
          ? "后台导出数据已导入过；已刷新看板。"
          : result.status === "SUCCESS"
            ? "同步完成；已刷新看板。"
            : `同步完成，状态：${result.status || "UNKNOWN"}；已刷新看板。`;
        await loadDashboard();
        setDashboardNotice(message, "ok");
        if ($("imports").classList.contains("active")) await loadImports();
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch (error) {
        setDashboardNotice(error.message, "error");
      } finally {
        button.textContent = originalText;
        button.disabled = false;
      }
    }
```

- [ ] **Step 3: Bind the click event**

Add this event binding beside the existing header action binding:

```javascript
    $("manualSyncBtn").addEventListener("click", runManualSync);
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dashboard_html.DashboardHtmlTest.test_header_has_manual_sync_button_that_calls_auto_import_run
```

Expected: `OK`.

### Task 3: Regression Verification

**Files:**
- Test only

- [ ] **Step 1: Run the dashboard HTML test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dashboard_html
```

Expected: `OK`.

- [ ] **Step 2: Run the full test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test*.py'
```

Expected: all tests pass.

- [ ] **Step 3: Review the final diff**

Run:

```powershell
git diff -- app/main.py tests/test_dashboard_html.py docs/superpowers/plans/2026-05-22-manual-sync-button.md
```

Expected: only the manual sync button, test, and implementation plan changed.
