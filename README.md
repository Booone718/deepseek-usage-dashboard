# DeepSeek Usage Dashboard

DeepSeek Usage Dashboard 是一个面向 DeepSeek 控制台用量导出文件的轻量级网页看板。上传 DeepSeek 官方导出的 ZIP 后，服务端会解析其中的 `amount` / `cost` CSV，把明细写入 SQLite，并在浏览器中展示费用、请求量、Token、账号、API Key、模型、部门和负责人等维度的分析图表。

## 功能

- 上传 DeepSeek 官方导出的 `.zip`
- 自动解析 `user_id`、`utc_date`、模型、API Key、Token 类型、请求量和费用
- 自动发现账号并生成占位账号映射
- 在页面维护 `user_id -> 账号名称 / 负责人 / 部门 / 备注`
- 支持账号映射 CSV 导入和导出
- 支持按账号、模型、API Key、日期、部门、负责人筛选
- 使用本地内置 ECharts，提供可悬停、可缩放、可点击联动筛选的趋势图、占比图、排行图和热力图
- 原始 ZIP 按导入批次归档，临时解压目录解析完成后自动清理
- 支持重复 ZIP 去重、导入批次撤销和过期原始 ZIP 清理
- 支持单账号模式按本地 cURL secret 每天定时拉取 DeepSeek 用量 ZIP 并导入
- 支持可选 HTTP Basic Auth

## 快速开始

### Docker Compose

```bash
docker compose up -d --build
```

默认访问地址：

```text
http://localhost:18080
```

`docker-compose.yml` 会把本地 `./data` 挂载到容器的 `/app/data`，所有上传文件、SQLite 数据库和导出文件都会保存在这个目录下。

### 本地开发

Linux / macOS：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

## 配置

可通过环境变量配置运行行为：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATA_DIR` | `data` | 数据目录 |
| `TZ` | `Asia/Shanghai` | 容器运行时区，也用于默认自动导入时区和服务端写入时间 |
| `UPLOAD_RETENTION_DAYS` | `365` | 原始 ZIP 保留天数 |
| `CLEANUP_ENABLED` | `true` | 是否启用过期原始 ZIP 清理 |
| `APP_PASSWORD` | 空 | 设置后启用 HTTP Basic Auth，用户名任意，密码为该值 |
| `AUTO_IMPORT_ENABLED` | `false` | 是否启用每天自动导入 |
| `DEEPSEEK_EXPORT_CURL_FILE` | `/app/secrets/deepseek-export.curl` | DeepSeek 用量导出请求 cURL secret 文件路径 |
| `DEEPSEEK_SINGLE_ACCOUNT_USER_ID` | 空 | 单账号自动导入时补写到明细中的固定 `user_id`，启用自动导入时必填 |
| `AUTO_IMPORT_DAILY_TIME` | `20:30` | 每天执行时间，格式 `HH:MM` |
| `AUTO_IMPORT_TIMEZONE` | `TZ` 的值，未设置 `TZ` 时为 `Asia/Shanghai` | 自动导入使用的时区，可覆盖容器运行时区 |

导入记录的服务端写入时间使用容器当前时区；页面展示时按访问者浏览器当前时区格式化为 `YYYY-MM-DD HH:mm:ss`。

可复制 `.env.example` 作为本地配置参考。

## 单账号自动导入

自动导入使用服务器本地 secret，不把 DeepSeek 登录态提交到 Git。配置步骤：

1. 在浏览器登录 DeepSeek 开放平台，进入 `https://platform.deepseek.com/usage`。
2. 选择月份并点击一次导出，在开发者工具 Network 中找到导出 ZIP 的请求。
3. 复制该请求为 cURL，写入服务器本地 `secrets/deepseek-export.curl`。
4. 在服务器本地配置 `AUTO_IMPORT_ENABLED=true` 和 `DEEPSEEK_SINGLE_ACCOUNT_USER_ID`。
5. 重启服务。系统会按 `AUTO_IMPORT_DAILY_TIME` 每天执行，也可以调用 `POST /api/auto-import/run` 手动触发一次。

运行时会按 `AUTO_IMPORT_TIMEZONE` 自动把 cURL 导出 URL 中的 `month` / `year` 参数改为当前月份；服务器本地的 cURL secret 只作为登录态和导出接口模板保存，不需要每月手工改月份。

`secrets/` 默认被 `.gitignore` 忽略。cURL 文件中可能包含 Cookie、CSRF token 等登录态，只能保存在服务器本地，不能提交到仓库、镜像或日志。

## 数据目录

默认数据目录结构：

```text
data/
  uploads/raw/       # 原始 ZIP，按导入批次保存
  tmp/auto-import-downloads/ # 自动导入下载的临时 ZIP
  tmp/extract/       # 临时解压目录，解析完成后删除
  db/                # SQLite 数据库
  exports/           # 预留导出目录
  logs/              # 预留日志目录
```

业务数据存储在：

```text
data/db/deepseek_usage.db
```

`data/` 是运行数据目录，默认不会提交到 Git。

## 账号映射 CSV

可在页面导入或导出账号映射。CSV 表头：

```csv
user_id,account_name,owner,department,remark,enabled
19a841e5-2ec3-453d-86c7-468e48759345,DeepSeek账号01,张三,研发一组,生产账号,1
```

如果上传的用量数据里出现新的 `user_id`，系统会自动创建占位账号：

```text
账号名称：未命名账号-xxxxxxxx
备注：自动发现，待维护
```

## 反向代理

应用支持通过反向代理部署在子路径下，例如 `/deepseek-usage/`。代理需要把页面、`/api/*` 和 `/static/*` 请求转发到同一个应用实例。

## 许可证

本项目使用 MIT License。

仓库内置的 ECharts 文件遵循其上游许可证，详见 `app/static/vendor/echarts.LICENSE`。
