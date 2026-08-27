# SpiderFly

SpiderFly 是从“例子”任务调度项目中提取并二创的本地 Python 管理工具。它删除了 Agent、配对码、设备和设备组，直接在当前 Windows 电脑上启动 Python 脚本。

## 当前功能

- 创建、编辑、启用、停用和删除 Python 任务。
- 提供类似影刀“常规任务计划”的统一调度中心，可按任务名称、启用状态、触发方式和应用名称筛选。
- 支持手动、单次、固定间隔、每日和每周五种触发方式，自动计算并显示下次运行时间。
- 同一个 `.py` 应用可以创建多个不同时间的任务计划。
- 使用当前 Python 或任务指定的解释器直接执行 `.py` 文件。
- 程序运行期间持续保存 `stdout` 和 `stderr`。
- 保存状态、退出码、开始时间、结束时间和真实耗时。
- 同一任务避免重复并发执行。
- 可选的任务级超时；`0` 表示不限时。
- 每次运行结束最多发送一条飞书消息：
  - 成功：一句文字。
  - 失败或超时：一条文字与截图组合的富文本消息。
- Kocotree UI 风格的运行总览、任务管理、执行记录和通知设置页面。

## 一键启动

双击：

```text
start.bat
```

首次运行会自动创建 `.venv`、安装后端依赖、安装前端依赖并构建页面。之后访问：

```text
http://127.0.0.1:8000
```

项目初始化时会自动创建“你好 flyyang”示例任务，脚本为 `sample_scripts/1.py`。

## 飞书通知（可选）

不配置飞书也可以完整运行任务和查看日志。需要通知时，将 `.env.example` 复制为 `.env`，填写：

```env
FEISHU_APP_ID=你的应用ID
FEISHU_APP_SECRET=你的应用密钥
FEISHU_RECEIVER_ID=接收人的open_id
FEISHU_RECEIVER_ID_TYPE=open_id
```

推荐直接配置 `open_id`。使用手机号时将类型改为 `mobile`，飞书应用还需要通讯录查询权限。

如果需要沿用项目根目录 `验证码转发.py` 中现有的飞书应用配置，可执行：

```powershell
.\.venv\Scripts\python.exe scripts\import_feishu_config.py --receiver 你的手机号
```

迁移脚本不会在终端输出应用密钥，生成的 `.env` 已被 `.gitignore` 排除。

## 开发模式

后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

前端：

```powershell
cd frontend
npm run dev
```

前端开发地址为 `http://127.0.0.1:5173`，请求会代理到后端 `8000` 端口。

## 目录

```text
spiderFly/
├── backend/            FastAPI、本地执行、SQLite、飞书通知
├── frontend/           Vue 与 Kocotree UI
├── sample_scripts/     可直接运行的示例脚本
├── data/               首次运行后生成的本地数据库
├── .env.example        飞书配置模板
└── start.bat           一键启动
```
