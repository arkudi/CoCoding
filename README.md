# CoCoding

## Git 仓库

https://github.com/arkudi/CoCoding

## 如何运行

运行环境：Windows 10/11、Python 3.11 及以上、Node.js 24.15 及以上、npm，以及可用的 DeepSeek API Key。

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

在 `.env` 中填写 `DEEPSEEK_API_KEY`，然后启动后端：

```powershell
python -m uvicorn app.main:app --app-dir backend --reload
```

另开一个 PowerShell 窗口启动前端：

```powershell
Set-Location frontend
npm install
npm run dev
```

浏览器访问 http://127.0.0.1:5173 。

## 特色功能

- 通过 Windows 原生窗口选择项目目录，并优先打开当前项目位置。
- Agent 自动生成任务名称，自主判断完成时机，最多共享 300 次工具调用。
- Manager 可向 Explorer、Implementer、Reviewer 分派任务，修改后必须经过独立审查。
- 实时展示执行过程、工具调用和工作区文件；文件夹支持展开与折叠。
- 支持代码预览、统一 Diff、Markdown 结果、任务取消及历史任务删除。
- 使用 SQLite 保存任务、消息、工具证据和文件变更，并验证测试及完成声明。

## 其它说明

任务删除只清理 CoCoding 记录，不会删除项目文件。Agent 命令在本机运行且不具备沙箱隔离，请只选择可信目录，不要提交 `.env` 或 API Key。
