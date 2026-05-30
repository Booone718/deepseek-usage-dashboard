# DeepSeek Usage Dashboard Frontend Redesign

Date: 2026-05-30

## Goal

Redesign the frontend presentation without removing the existing dashboard. The new default experience should focus on API Key usage analysis, because in this company setup each API Key name represents an employee. DeepSeek account is an organizational grouping and resource boundary, not the primary analysis subject.

The implementation should support two dashboard views:

- New analysis view: API Key/employee usage analysis, with automatic single-account or multi-account presentation.
- Classic dashboard view: the current dashboard presentation, kept as a fallback while the new view is evaluated.

Users should be able to switch between the two views at any time. The selected view should persist locally.

## Product Context

The company may have multiple DeepSeek accounts. Each DeepSeek account can contain up to 20 API Keys, and each API Key is assigned to one employee. Most of the time the user only has data for one account, because automatic export currently supports one DeepSeek account. The UI therefore needs to work well in single-account mode today while still being ready for multi-account data later.

API Key names are employee names. Do not add an employee/API Key mapping workflow. Existing account mapping can remain for naming DeepSeek accounts, owners, departments, or remarks when multiple account data exists.

## Visual Direction

Use a light, dense, professional operations-console style:

- Background: quiet light gray-blue.
- Surfaces: white panels with restrained borders.
- Navigation/header: deep blue-gray or ink tone.
- Primary accent: blue-green.
- Cache success: green.
- Anomaly or sharp increase: red.
- Cost warning: amber.

Avoid a marketing-style landing page, decorative hero sections, large gradients, and dashboard-as-TV-wall styling. The page should feel like a practical internal analytics tool: readable tables, clear sorting, visible filters, compact charts, and fast drill-down.

## View Switching

Add a segmented control near the dashboard header:

- New analysis
- Classic dashboard

Behavior:

- The classic dashboard is the current dashboard layout and functionality.
- The new analysis view is added alongside it.
- Switching views should not refetch unrelated data if the current dashboard payload is already available.
- Persist the selected view in localStorage.
- Support URL parameters such as `?view=classic` and `?view=analysis` for direct access.
- Keep classic view available after implementation so the user can fall back if the new design is not satisfactory.

## Account Mode

Account mode is automatic, not manually selected.

Rules:

- If all imported active data contains one distinct DeepSeek account, use single-account mode.
- If imported active data contains two or more distinct DeepSeek accounts, use multi-account mode.
- If a user filters a multi-account dataset down to one account, the system remains in multi-account mode because the broader dataset supports account comparison.

The current backend already returns `account_mode` from `/api/dashboard`. The frontend should use that value and avoid adding a manual account-mode toggle.

## New Analysis View

### Primary Layout

The first screen should prioritize API Key/employee analysis.

Recommended structure:

1. Header summary
   - Data updated time
   - Current mode badge: single account or multi account
   - Manual sync button
   - Upload shortcut
   - View switcher

2. Filter bar
   - Date range and quick presets
   - API Key/employee search
   - Model filter
   - Account filter only in multi-account mode
   - Department and owner filters only in multi-account mode and only when account metadata contains non-empty department or owner values

3. KPI strip
   - Total tokens
   - Total cost
   - Requests
   - API Key/employee count
   - Average cache hit rate
   - Output token share
   - Model count
   - Multi-account mode only: account count

4. API Key/employee table
   - This is the core component of the new view.
   - Each row represents one API Key/employee.
   - It should appear before large chart panels.
   - It should support sorting by usage, cost, requests, cache rate, output share, and trend.

5. Selected API Key detail panel
   - When no API Key is selected, show the top API Key by tokens or a concise empty prompt.
   - When a row is selected, show detailed charts and breakdowns for that API Key.

6. Supporting analysis panels
   - Model distribution
   - Token type structure
   - Daily trend table or chart
   - Multi-account-only account analysis panels

### API Key/Employee Table Columns

Columns should include:

- API Key / employee name
- Account name, multi-account mode only or secondary in single-account mode
- Total tokens
- Cost
- Requests
- Cache hit rate
- Output token share
- Primary model
- Trend indicator
- Last active date, omitted in the first slice unless it can be derived from the current payload without a backend change

Cache hit rate is computed from input cache hit and input cache miss tokens:

`cache_hit_tokens / (cache_hit_tokens + cache_miss_tokens)`

Rows with very low cache hit rate or sharp negative cache-rate movement should be visually distinguishable but not noisy.

### API Key Detail Panel

For the selected API Key/employee, show:

- Daily token and cost trend
- Cache hit rate trend
- Requests trend, if it helps diagnose usage spikes
- Model distribution
- Input cache hit, input cache miss, and output token structure
- Multi-account mode only: account contribution if the same Key name appears under multiple accounts

The detail panel should make it easy to answer:

- How much did this employee use?
- Did their usage increase recently?
- Is their cache hit rate healthy?
- Which model is driving the usage?
- Which account does the usage belong to?

## Single-Account Mode

Single-account mode should remove analysis that does not add value when there is only one DeepSeek account.

Hide or de-emphasize:

- Account filter
- Account summary cards
- Account x API Key heatmap
- Department cost distribution
- Owner cost distribution
- Account mapping tab as a primary dashboard tab

Keep:

- API Key/employee table
- API Key detail panel
- Trend analysis
- Cache rate analysis
- Model analysis
- Token type structure
- Upload, manual sync, and import history

The single-account experience should feel complete, not like a disabled multi-account dashboard.

## Multi-Account Mode

Multi-account mode should keep the API Key/employee table as the main component and add account grouping and comparison.

Add or reveal:

- Account filter
- Account count KPI
- Account summary table
- Account ranking by tokens, cost, requests, and average cache rate
- Account x API Key heatmap
- Account contribution inside selected API Key detail
- Account management or mapping navigation if account names need maintenance

Account is a grouping dimension. It should explain and organize API Key usage, but it should not replace API Key/employee as the main analysis subject.

## Classic Dashboard View

The classic view should preserve the current dashboard presentation as much as practical:

- Current KPI grid
- Current trend chart
- Current model share chart
- Current API Key rank chart
- Current token mix chart
- Current tables
- Current upload, account mapping, and import record workflows

Minor shared styling updates are acceptable only if they do not materially change the classic layout. The purpose of classic view is fallback, so it should remain recognizable.

## Existing Feature Treatment

Keep these capabilities:

- ZIP upload
- Multiple ZIP upload progress
- Auto import status and manual sync
- Import history and duplicate handling
- Cleanup for old raw ZIP files
- Account naming/mapping for DeepSeek accounts
- CSV import/export for account mapping, if still useful
- ECharts interactions and click-to-filter behavior where applicable

Reframe these capabilities:

- API Key summary becomes the main homepage table in the new view.
- Model, token mix, and trend charts become supporting explanations for selected API Key or current filters.
- Account mapping is account metadata management, not employee mapping.

## Data and API Notes

Prefer using the existing `/api/dashboard` payload for the first implementation slice. It already includes:

- `account_mode`
- `global_account_count`
- `kpi`
- `by_key`
- `by_key_model`
- `by_account`
- `by_model`
- `token_mix`
- `model_account`
- `trend`
- `trend_by_model`
- `accounts`
- `models`

If the selected API Key detail cannot be derived cleanly from the current payload, add a focused backend endpoint later, such as:

`GET /api/dashboard/key-detail?key=<name>&date_from=...&date_to=...&user_id=...`

Do not add that endpoint until the frontend need is proven during implementation.

## Testing and Verification

At minimum, verify:

- Existing backend tests still pass.
- Classic view still renders recognizable current dashboard content.
- New analysis view renders with the existing dashboard payload.
- Single-account mode hides account-only modules.
- Multi-account mode shows account-only modules.
- View selection persists through reload.
- Browser verification covers desktop and mobile widths.

## Implementation Decisions

- The new analysis view should be the default dashboard view once implemented, because the purpose of this change is to evaluate the new API Key/employee workflow. The classic dashboard remains one click away through the view switcher.
- Trend-change indicators should be computed in the frontend for the first slice from the existing dashboard payload. Do not add backend trend metrics unless the frontend implementation proves the current payload is insufficient.
