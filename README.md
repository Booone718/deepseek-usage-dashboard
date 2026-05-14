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
| `UPLOAD_RETENTION_DAYS` | `365` | 原始 ZIP 保留天数 |
| `CLEANUP_ENABLED` | `true` | 是否启用过期原始 ZIP 清理 |
| `APP_PASSWORD` | 空 | 设置后启用 HTTP Basic Auth，用户名任意，密码为该值 |

可复制 `.env.example` 作为本地配置参考。

## 数据目录

默认数据目录结构：

```text
data/
  uploads/raw/       # 原始 ZIP，按导入批次保存
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
