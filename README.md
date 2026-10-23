# 行间 · 城市旅行 Agent

把想去的地方变成走得通的行程，并在计划变化时协助调整。

当前已完成可本地运行的开发版：需求表单 → 工具与候选选择 → 排程和独立校验 → 行程预览 → 用户确认保存 → 延误调整与版本恢复。

默认演示模式不需要 API Key。**地点时间和坐标为自建夹具，路线为估算，天气未知；不能直接当作真实出行攻略。**

## 本地启动（Windows PowerShell）

需要 Node.js 22.12+ 或 24+、Python 3.12+。安装脚本支持通过 `-Python` 指定解释器，也会尝试当前桌面环境内置的 Python。

在仓库根目录执行：

```powershell
.\scripts\setup.ps1
.\scripts\dev.ps1
```

当前工作区已安装依赖，可以直接执行第二条。页面为 [http://127.0.0.1:5173](http://127.0.0.1:5173)，接口文档为 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

开发脚本在当前终端等待，按 Ctrl+C 关闭它启动的两个子进程；日志位于 `logs/`。如果端口被占用，先关闭已有服务。

也可以在两个终端分别启动：

```powershell
# 终端一，仓库根目录
& backend/.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

# 终端二
Set-Location frontend
npm run dev
```

## 体验路径

1. 保留默认地点，勾选固定预约，点击“生成我的行程”。
2. 查看时间轴、路线示意和行程检查，再保存演示行程。
3. 点击“晚出发 40 分钟”，检查预约是否保留、其余活动如何变化，确认更新。
4. 刷新页面继续查看，打开版本记录恢复历史安排。
5. 将结束时间改成 09:10 后生成新行程，观察无法满足条件时如何阻止保存。

## 目的地与用户 API 配置

目的地支持国家/地区 → 省份、州或行政区 → 城市三级下拉，并可在当前行政区检索中文或英文城市名。地理目录在本地提供，不需要地图 Key；切换城市会清空旧地点、预约与目录引用，并使用目的地当地时区。

点击页面右上角「API 设置」，填写自己的服务信息：

- 模型：选择使用模型，填写公开 HTTPS 服务地址、模型名称、API Key。程序追加 `/chat/completions`，服务需支持工具调用和 JSON 输出。
- 高德：填写 Web 服务 Key 后，在目的地下面点击「查询城市真实地点」，再选择想去的地方。加载的 POI 与这次旅行一同保存，后续调整使用同一目录。
- 浏览器地图：可选填单独的 JS API Key 与安全密钥；未填时保留路线示意。修改浏览器地图凭据后页面会刷新。
- 可分别测试模型和高德连接。模型连接测试只验证基础响应，工具兼容性在实际规划时检查；测试及规划会消耗用户自己的额度。

配置只保存于当前标签页的 `sessionStorage`，不写入 `localStorage`、行程、任务或数据库。可一键清除；不同浏览器会话的配置互不覆盖。Key 经当前请求交给后端用于转发；对外部署应使用 HTTPS。

**网页请求不会继承服务器 `.env` 中的模型、地图 Key。** 没有用户配置时固定使用演示模式。原有环境凭据仅供明确执行的命令行工具验证。

模型自定义地址仅支持公开 HTTPS/443，不接受内网、账号或查询参数。服务端将校验后的公网 IP 用于连接，并保留原域名的 TLS SNI 与 Host，不跟随跳转。

地理目录覆盖全球国家/地区，但当前高德适配器接入的是中国大陆标准接口。境外可选择目的地并体验示意排程，真实地点、路线与天气暂不支持。杭州保留原演示夹具；其他城市未查询真实地图时使用明确标注的占位活动，不冒充景点。真实 POI 的营业、预约和建议停留时间仍需核实。

目录来源与数据许可见 [地理数据说明](data/geography/ATTRIBUTION.md)，固定版本与转换脚本支持复现。目录包含城市、城镇及部分区级地点，数据覆盖不等于地图服务覆盖。

命令行提供方检查仍可通过根目录 `.env` 显式配置后执行 `backend/.venv/Scripts/python.exe scripts/smoke_tools.py`；它与网页配置独立，未配置的提供方显示 `SKIPPED`。

## 测试与评测

```powershell
& backend/.venv/Scripts/python.exe -m pytest backend/tests -q
& backend/.venv/Scripts/python.exe -m ruff check backend/app backend/tests backend/migrations scripts
& backend/.venv/Scripts/python.exe scripts/evaluate.py

Set-Location frontend
npm run format:check
npm run build
npx playwright install chromium
npm run test:e2e
```

浏览器测试会自动启动未运行的服务。使用正在运行的服务时，应确保它处于演示模式。固定数据结果写入被 Git 忽略的 `evals/results/`，不是实测旅行成功率。

后端依赖锁定在 `backend/requirements.lock`，前端锁定在 `frontend/package-lock.json`。

## 数据库

默认数据库是 `data/local.sqlite3`，本地数据、密钥和原始评测输出不会提交。

安装脚本执行初始迁移。手动迁移命令：

```powershell
& backend/.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
```

保留 PostgreSQL 配置入口：在根目录 `.env` 设置 `DATABASE_URL=postgresql+psycopg://...`，再执行迁移。当前未验证真实 PostgreSQL 环境。

## 代码结构

| 目录 | 内容 |
| --- | --- |
| `backend/app/agent.py` | LangGraph 阶段编排 |
| `backend/app/llm.py` | 有界模型工具循环、允许列表和候选校验 |
| `backend/app/tools.py` | 演示、高德城市地点、坐标、路线与天气适配 |
| `backend/app/geography.py` | 国家、省州、城市目录与归属校验 |
| `backend/app/user_config.py` | 用户请求凭据与模型公网转发 |
| `backend/app/planner.py` | 固定活动分段与启发式排程 |
| `backend/app/validation.py` | 独立约束检查 |
| `backend/app/services.py` | 任务、预览、幂等确认与版本事务 |
| `backend/migrations/` | Alembic 数据库迁移 |
| `frontend/src/` | 表单、时间轴、地图、修改预览与版本交互 |
| `data/curated/` | 自建演示地点夹具 |
| `data/geography/` | 固定版本的地理目录及来源许可 |
| `evals/datasets/` | 固定排程冒烟任务 |
| `scripts/` | 安装、启动、评测与真实工具检查 |

## 实施资料

- [初版实施计划](docs/IMPLEMENTATION_PLAN.md)
- [当前完成情况、限制与下一步](docs/IMPLEMENTATION_STATUS.md)
- [为什么先使用本地演示链路](docs/decisions/0001-local-demo-first.md)

后续重点是核验真实数据与工具、完善任意预约和对话需求管理、增加校验驱动的修复循环，再补充 PostgreSQL 与持久化 worker。完整的多轮会话和失败恢复尚未完成。
