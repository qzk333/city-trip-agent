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

## 可选真实服务配置

将根目录 `.env.example` 复制为 `.env`。两个模式互相独立，修改后重启后端。

```dotenv
# 使用高德交通与天气查询
TOOL_PROVIDER=amap
AMAP_API_KEY=填写服务端Web服务Key

# 模型通过工具查询演示目录，然后输出候选排序
PLANNING_MODE=llm
LLM_BASE_URL=填写兼容ChatCompletions的服务地址前缀
LLM_API_KEY=填写模型Key
LLM_MODEL=填写模型名称
```

程序向 `LLM_BASE_URL` 追加 `/chat/completions`；模型服务需支持 `tools`、`tool_choice` 与 JSON 对象输出。模型不会负责数值排程，也不会获准修改硬约束。

即使接入真实交通工具，地点营业时间等仍来自演示夹具；页面会继续显示待核验提示。没有 Key 或调用失败时返回明确错误，不静默使用演示路线。

若需要真实地图，将 `frontend/.env.example` 复制为 `frontend/.env`，填写高德 JS API Key 与对应安全配置，再重启前端。未配置时使用标注为“非导航地图”的示意图。浏览器 Key 与服务端 Key 分开配置。

执行真实接口冒烟检查：

```powershell
& backend/.venv/Scripts/python.exe scripts/smoke_tools.py
```

未配置的提供方会显示 `SKIPPED`，不代表验证通过。已配置的真实模型请求会消耗服务额度。

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
| `backend/app/tools.py` | 演示与高德路线/天气适配 |
| `backend/app/planner.py` | 固定活动分段与启发式排程 |
| `backend/app/validation.py` | 独立约束检查 |
| `backend/app/services.py` | 任务、预览、幂等确认与版本事务 |
| `backend/migrations/` | Alembic 数据库迁移 |
| `frontend/src/` | 表单、时间轴、地图、修改预览与版本交互 |
| `data/curated/` | 自建演示地点夹具 |
| `evals/datasets/` | 固定排程冒烟任务 |
| `scripts/` | 安装、启动、评测与真实工具检查 |

## 实施资料

- [初版实施计划](docs/IMPLEMENTATION_PLAN.md)
- [当前完成情况、限制与下一步](docs/IMPLEMENTATION_STATUS.md)
- [为什么先使用本地演示链路](docs/decisions/0001-local-demo-first.md)

后续重点是核验真实数据与工具、完善任意预约和对话需求管理、增加校验驱动的修复循环，再补充 PostgreSQL 与持久化 worker。完整的多轮会话和失败恢复尚未完成。
