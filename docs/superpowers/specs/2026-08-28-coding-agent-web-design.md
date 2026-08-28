# 本地 Web 编程智能体设计

## 1. 项目目标

本项目实现一个供课程展示的本地编程智能体。用户在 Vue 网页中选择一个本地工作区并提交编程任务；FastAPI 后端调用 DeepSeek，执行模型请求的文件和命令工具，并把执行过程实时展示在网页中。任务结束后，用户可以查看最终说明、修改文件和统一 diff；刷新页面后仍可查看历史任务。

项目自行实现对话历史、上下文管理、工具定义与执行、模型输出解析、循环终止和错误处理。只使用模型厂商兼容客户端，不依赖任何 Agent 框架或服务端托管的文件、代码执行能力。

## 2. 范围

### 2.1 首版功能

- 创建和查看任务会话。
- 选择或配置一个本地工作区。
- 提交编程任务并实时查看执行过程。
- 通过 DeepSeek 原生 Tool Calling 驱动 Agent 循环。
- 提供列目录、读文件、写文件、精确替换、执行命令和获取 diff 的本地工具。
- 展示文件树、只读代码预览、工具记录、命令输出和 diff。
- 使用 SQLite 保存任务历史和执行证据。
- 支持最大步数、取消、命令超时、输出截断和错误展示。

### 2.2 非目标

首版不实现网页代码编辑器、多用户登录、公网部署、多智能体、Docker 沙箱、自动 Git 分支管理、向量数据库、RAG 或多个任务并行执行。

## 3. 总体架构

系统采用单机前后端架构：

```text
Vue 3 + Vite + TypeScript
        | REST + WebSocket
FastAPI application
        |-- Session / Run API
        |-- Agent loop
        |-- DeepSeek client
        |-- Tool registry and executor
        |-- Workspace and diff services
        |-- Event publisher
        `-- SQLite
                 |
          Local workspace
```

开发时 Vue 和 FastAPI 分别启动，Vite 代理 `/api` 与 `/ws` 请求。答辩构建时 FastAPI 同时托管 Vue 的静态产物，以一个本地地址运行完整应用。

## 4. 前端设计

主页面采用三栏布局：

- 左栏：新建任务、历史任务、运行状态。
- 中栏：用户消息、Agent 状态、工具调用、命令输出和最终回答组成的时间线。
- 右栏：工作区文件树、只读文件内容和本次运行 diff。
- 窄屏时右栏变为可切换抽屉，但首版以桌面浏览器为主要目标。

前端使用 Vue Router 管理任务页面，使用 Pinia 保存当前会话、运行状态、事件时间线、文件树和 diff。REST 负责创建与恢复资源，WebSocket 只传输实时事件。WebSocket 断开后，页面通过 REST 重新获取持久化状态并再次连接，不假设事件流可以完整重放。

## 5. 后端模块

```text
backend/app/
  main.py
  api/
  agent/
    loop.py
    model.py
    prompts.py
    context.py
  tools/
    registry.py
    files.py
    shell.py
  services/
    workspace.py
    diff.py
  db/
  events.py
```

- `AgentLoop`：调用模型、解析工具请求、执行工具、回传结果并判断终止。
- `DeepSeekClient`：封装 OpenAI 兼容接口，不包含循环逻辑。
- `ContextManager`：维护原始消息、长度预算和结构化摘要。
- `ToolRegistry`：集中保存工具 JSON Schema、参数模型和处理函数。
- `WorkspaceService`：解析路径并强制所有文件操作位于工作区内。
- `DiffService`：记录初始文件状态并生成统一 diff。
- `EventPublisher`：向当前会话的 WebSocket 订阅者广播标准事件。
- 数据访问层：管理 SQLite 事务，不让 API 或 Agent 直接拼接 SQL。

## 6. Agent 循环

一次运行采用以下流程：

1. 保存用户任务并创建状态为 `running` 的 Run。
2. 组装主 System Prompt、上下文摘要、最近消息和当前任务。
3. 调用 DeepSeek Chat Completions，并提供工具 Schema。
4. 若模型返回工具调用，先解析 JSON，再用 Pydantic 校验参数。
5. 执行有效工具，记录调用、结果和耗时，并通过 WebSocket 广播事件。
6. 把 assistant 工具请求和对应 tool 结果追加到模型历史，返回步骤 3。
7. 若模型返回最终文本，保存回答、生成 diff，并将 Run 标记为 `completed`。

循环在下列任一条件下终止：模型给出最终回答、达到最大步数、用户取消、连续 API 失败超过限制、不可恢复的内部错误或进程重启。进程启动时，遗留的 `running` Run 被标记为 `interrupted`，不自动恢复中间执行。

## 7. 工具

首版工具如下：

- `list_files(path)`：列出目录，默认忽略缓存和构建产物。
- `read_file(path, start_line, end_line)`：分段读取 UTF-8 文本。
- `write_file(path, content)`：创建或完整重写文本文件。
- `replace_in_file(path, old_text, new_text)`：只在旧文本唯一匹配时替换。
- `run_command(command, timeout)`：以工作区为当前目录执行命令。
- `get_diff()`：返回当前 Run 产生的统一 diff。

工具统一返回：

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "meta": {"duration_ms": 12, "truncated": false}
}
```

失败时 `ok` 为 `false`，`error` 包含稳定的错误代码和面向模型的说明。文件内容和命令输出都有字符上限，超出部分截断并设置 `truncated`。

## 8. 数据与持久化

SQLite 保存五类记录：

- `sessions`：标题、工作区路径和时间戳。
- `runs`：用户任务、状态、模型、Prompt 版本、步数、起止时间和错误。
- `messages`：用户消息、可展示的 Agent 消息、工具结果摘要和时间戳。
- `tool_calls`：名称、参数、状态、结果摘要、耗时和关联 Run。
- `file_changes`：相对路径、操作类型、修改前后哈希和统一 diff。

数据库不保存 API Key、模型隐藏推理、整个项目副本、无限长度命令输出或全部临时 WebSocket 事件。真实源文件始终位于工作区。

所有实时事件使用统一信封：

```json
{
  "event_id": "uuid",
  "type": "tool.completed",
  "session_id": "uuid",
  "run_id": "uuid",
  "timestamp": "ISO-8601",
  "data": {}
}
```

事件类型包括 `run.started`、`agent.status`、`assistant.delta`、`assistant.completed`、`tool.started`、`tool.output`、`tool.completed`、`file.changed`、`run.completed` 和 `run.failed`。

## 9. API

首版 REST API：

- `POST /api/sessions`
- `GET /api/sessions`
- `GET /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/runs`
- `POST /api/runs/{run_id}/cancel`
- `GET /api/workspace/tree`
- `GET /api/workspace/file`
- `GET /api/runs/{run_id}/changes`

实时连接使用 `/ws/sessions/{session_id}`。提交 Run 的 HTTP 请求立即返回 `run_id`，实际执行在后端任务中继续；浏览器连接是否存在不决定 Run 的生命周期。

## 10. Prompt 设计

主 System Prompt 规定 Agent 目标、工作区边界、工具使用、最小改动、测试要求、禁止虚构执行结果、错误恢复、最大步数和最终报告格式。工作区、操作系统和最大步数由后端动态注入。

上下文超过预算时，独立摘要 Prompt 生成结构化 JSON，保留用户目标、约束、重要发现、文件变化、命令与测试、错误和剩余工作。模型后续输入由主 Prompt、摘要、最近 6 至 10 条原始消息和当前任务组成。摘要不得添加历史中不存在的事实。

主 Prompt 使用版本号 `coding_agent_v1`，对应版本保存到 Run。工具说明只存在于 Tool Calling Schema，不重复塞入主 Prompt。

## 11. 安全与错误处理

- API Key 仅从 `DEEPSEEK_API_KEY` 环境变量读取，模型和 API 地址可配置。
- 所有文件路径先解析为绝对路径，再验证其位于工作区根目录内。
- 命令固定以工作区为当前目录，设置超时和输出上限。
- 明显危险的系统级命令由后端拒绝；本项目只面向用户自己的本地可信工作区。
- 工具参数在执行前校验，未知工具、无效 JSON 和额外参数均返回结构化错误。
- 文件修改和数据库变更的记录顺序确保失败写入不会被标记为成功。
- API 错误采用有限次数的指数退避；参数错误交给模型修正，不重复完全相同的失败调用。
- 前端展示可操作错误信息，但不展示 API Key、内部堆栈或模型隐藏推理。

## 12. 测试策略

后端单元测试覆盖：路径穿越、读写与精确替换、命令超时、输出截断、无效工具参数、最大步数、取消和上下文摘要选择。Agent 循环使用假的模型响应验证多轮工具调用，不依赖真实 API。

集成测试覆盖：创建会话、提交 Run、事件发布、持久化记录和重启后中断状态。DeepSeek 只保留一个可手动运行的真实接口冒烟测试。

前端测试重点覆盖 Store 的事件归并与状态切换。最终进行一次固定演示验收：智能体读取带缺陷的小项目、修改文件、运行测试、展示成功结果和 diff，刷新后历史仍可查看。

## 13. 实施顺序

1. 建立后端与前端工程骨架、配置和数据库基础。
2. 以测试驱动方式完成工作区、文件、命令和 diff 工具。
3. 使用假模型完成 Agent 循环、事件和持久化。
4. 接入 DeepSeek 并在后端完成端到端冒烟运行。
5. 实现 REST、WebSocket 和 Vue 三栏界面。
6. 完成上下文摘要、取消、错误恢复和演示项目。
7. 构建前端静态产物，由 FastAPI 托管，整理 README 和答辩流程。

## 14. 验收标准

用户能在本地网页创建会话并提交编程任务。系统自动检查指定工作区、修改代码并运行验证命令；页面实时显示工具步骤和输出，结束后显示最终报告与 diff。刷新或重启应用后，已完成任务仍可查看；路径越界、命令超时、API 错误和最大步数均以可理解的状态结束且不会泄露凭据。
