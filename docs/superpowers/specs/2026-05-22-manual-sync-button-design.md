# DeepSeek 用量看板立即同步按钮设计

## 背景

系统已经支持单账号模式每天定时从 DeepSeek 后台导出用量 ZIP 并导入，也已经提供 `POST /api/auto-import/run` 用于手动触发一次后台导出导入。当前页面没有入口，用户需要通过接口调用才能立即同步。

本次改造在页面右上角 `上传数据` 旁边增加一个 `立即同步` 按钮，点击后复用现有手动触发接口完成后台导出和导入。

## 交互设计

- 顶部右上角操作区展示两个按钮：`立即同步` 和 `上传数据`。
- `立即同步` 使用主要操作样式，位置在 `上传数据` 旁边。
- 点击 `立即同步` 后按钮禁用，按钮文案临时变为 `同步中...`，避免重复点击。
- 同步成功后刷新看板数据，并在看板顶部提示同步结果。
- 如果接口返回 `SUCCESS`，提示 `同步完成；已刷新看板。`
- 如果接口返回 `DUPLICATE`，提示 `后台导出数据已导入过；已刷新看板。`
- 如果接口返回其他状态，提示 `同步完成，状态：<status>；已刷新看板。`
- 如果接口失败，展示后端返回的脱敏错误信息，按钮恢复可点击。

## 后端合同

本次不新增后端接口。页面直接调用已有接口：

```text
POST /api/auto-import/run
```

接口现有行为保持不变：

- 如果定时导入调度器已启动，调用 `auto_import_scheduler.run_once()`。
- 如果调度器未启动，直接调用 `_run_auto_import_job()`。
- 配置缺失、secret 文件缺失、下载或解析失败时返回错误，错误信息由后端脱敏。

## 前端实现

修改 `app/main.py` 内联 HTML/JavaScript：

- 在 header 的 `.header-actions` 内加入 `id="manualSyncBtn"` 的按钮，文本为 `立即同步`。
- 增加 `runManualSync()` JavaScript 函数：
  - 保存按钮原始文案。
  - 禁用按钮并设置 `同步中...`。
  - `POST` 调用 `/api/auto-import/run`。
  - 根据返回 `status` 生成用户提示。
  - 调用 `loadDashboard()` 刷新看板。
  - 如果当前或用户随后打开导入记录页，可通过现有 `loadImports()` 刷新导入记录数据。
  - `finally` 中恢复按钮文案和可点击状态。
- 给 `manualSyncBtn` 绑定点击事件。

## 错误处理

- 使用现有 `api()` 包装函数读取 JSON 响应。
- 后端错误通过 `setDashboardNotice(error.message, "error")` 展示。
- 不在前端记录或展示 cURL secret、Cookie、CSRF token 等敏感内容。

## 测试计划

- 更新 `tests/test_dashboard_html.py`：
  - 断言页面包含 `id="manualSyncBtn"` 和按钮文本 `立即同步`。
  - 断言 JavaScript 调用 `api("/api/auto-import/run", { method: "POST" })`。
  - 断言同步中禁用按钮并显示 `同步中...`。
  - 断言成功后调用 `loadDashboard()` 并展示同步完成提示。

本次不需要新增仓储层或后端 API 测试，因为后端手动触发接口已经存在，页面只新增调用入口。
