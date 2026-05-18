# DeepSeek 用量看板账号模式设计

## 背景

当前看板以 DeepSeek 导出数据中的 `user_id` 作为账号维度，支持账号映射、账号筛选、账号汇总和账号-模型热力图。现阶段用户导入的数据通常只有一个账号，但页面仍按多账号分析工具展示，账号对比类模块在单账号场景下价值较低。

本次改造支持两种展示模式：

- 单账号模式：全库成功导入数据中只有 0 或 1 个非空 `user_id`。
- 多账号模式：全库成功导入数据中有 2 个及以上非空 `user_id`。

模式判断基于全库成功导入数据，不受当前日期、模型、API Key、部门、负责人或账号筛选影响。

## 后端合同

`GET /api/dashboard` 在现有响应基础上新增字段：

```json
{
  "account_mode": "single",
  "global_account_count": 1
}
```

字段含义：

- `global_account_count`：全库成功导入数据中的 distinct 非空 `user_id` 数量。
- `account_mode`：当 `global_account_count <= 1` 时为 `single`，否则为 `multiple`。

现有统计字段、筛选参数和账号映射接口保持兼容。

## 前端展示

单账号模式下：

- 保留顶部“账号”筛选，继续展示“全部账号”和当前账号选项。
- 保留“账号映射”页，继续维护账号名称、负责人、部门、备注和启用状态。
- 隐藏“账号-模型热力图”模块。
- 隐藏“账号汇总”表。
- 保留“API Key 用量排行”。当前 DOM id 为 `accountRankChart`，但该模块实际展示 API Key 排行，不属于账号对比模块。

多账号模式下：

- 保持当前页面展示不变。
- 账号-模型热力图和账号汇总表继续显示。

## 数据与兼容性

本次不调整数据库结构，不迁移历史数据。现有 `usage_amount.user_id`、`account_mapping.user_id` 和导入去重逻辑保持不变。

如果还没有成功导入数据，`global_account_count` 为 0，页面按单账号模式处理，以避免展示空账号对比模块。

## 测试计划

- 仓储层测试：只有一个全局 `user_id` 时返回 `account_mode = single` 和 `global_account_count = 1`。
- 仓储层测试：全库有多个 `user_id`，即使当前筛选结果只剩一个账号，也返回 `account_mode = multiple`。
- 前端 HTML 测试：页面存在账号模式切换逻辑，单账号模式隐藏账号对比模块，同时账号筛选仍保留。
