# Streaming Assistant Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream user-visible DeepSeek answer text into the Vue timeline while preserving complete-message persistence, tool execution, Run reconciliation, and safe error behavior.

**Architecture:** `DeepSeekClient` consumes the OpenAI-compatible streaming iterator, emits visible content through a callback, and reconstructs the existing `AssistantTurn`, including fragmented tool calls. `AgentLoop` turns callbacks into transient WebSocket events; Pinia holds an in-memory draft that `Timeline` renders until terminal reconciliation supplies the durable final response.

**Tech Stack:** Python 3.11+, OpenAI Python SDK, FastAPI WebSockets, SQLAlchemy, pytest, TypeScript 5.8, Vue 3, Pinia 3, Vitest, Testing Library, Vite 7.

**Spec:** `docs/designs/2026-09-01-streaming-output-design.md`

## Global Constraints

- Stream only user-visible assistant content; never expose reasoning fields or chain-of-thought.
- Keep SQLite authoritative and persist only complete assistant messages, never individual deltas.
- Keep the existing single WebSocket transport and one-worker runtime.
- Reconstruct tool-call arguments fully before execution; never stream them to the UI.
- Retry transient provider failures only before any visible delta has been delivered.
- Use TDD and commit after each independently verified task.
- Do not run the optional DeepSeek smoke test without a newly rotated local key.
- Command execution remains unsandboxed and restricted to trusted workspaces and prompts.

## File Structure

- `backend/app/agent/types.py`: callback type and `ModelClient` signature.
- `backend/app/agent/provider.py`: chunk consumption, visible deltas, tool-call reconstruction, retry rules.
- `backend/app/agent/loop.py`: assistant lifecycle event ordering and persistence boundary.
- `backend/tests/agent/fakes.py`: deterministic callback-aware model fake.
- `backend/tests/agent/test_provider.py`: provider streaming and failure tests.
- `backend/tests/agent/test_loop.py`: event ordering and exactly-once persistence tests.
- `backend/tests/test_run_events.py`: WebSocket serialization integration test.
- `backend/tests/test_runs.py`, `backend/tests/agent/test_run_manager.py`: callback-aware local model doubles.
- `frontend/src/types/run.ts`: assistant event and draft payload types.
- `frontend/src/stores/runs.ts`: transient draft state and event lifecycle.
- `frontend/src/stores/runs.spec.ts`: accumulation, cleanup, validation, and reconciliation tests.
- `frontend/src/App.vue`: selected-draft wiring.
- `frontend/src/components/Timeline.vue`: live assistant bubble and cursor.
- `frontend/src/components/Timeline.spec.ts`: live and durable output tests.
- `frontend/src/styles.css`: reduced-motion-aware cursor styling.
- `README.md`: transient streaming versus durable state documentation.

---

### Task 1: Stream and reconstruct visible provider content

**Files:**
- Modify: `backend/app/agent/types.py`
- Modify: `backend/app/agent/provider.py`
- Modify: `backend/tests/agent/test_provider.py`

**Interfaces:**
- Consumes: chunks exposing `choices[0].delta.content`.
- Produces: `TextDeltaSink = Callable[[str], None]`.
- Produces: `ModelClient.complete(messages, tools, on_text_delta=None) -> AssistantTurn`.

- [ ] **Step 1: Write the failing stream test**

```python
def _content_chunk(content):
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def test_complete_streams_visible_content_and_reconstructs_turn():
    captured = {}
    stream = iter([_content_chunk("Hello"), _content_chunk(None), _content_chunk(" world")])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: captured.update(kwargs) or stream
    )))
    deltas = []

    turn = DeepSeekClient(client, "deepseek-v4-flash").complete(
        [{"role": "user", "content": "hello"}],
        [{"type": "function"}],
        on_text_delta=deltas.append,
    )

    assert captured["stream"] is True
    assert deltas == ["Hello", " world"]
    assert turn == AssistantTurn("Hello world")
```

- [ ] **Step 2: Run the test and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_provider.py::test_complete_streams_visible_content_and_reconstructs_turn -v
```

Expected: FAIL because `complete` has no callback parameter and does not request `stream=True`.

- [ ] **Step 3: Add the callback contract and minimal content loop**

```python
from collections.abc import Callable

TextDeltaSink = Callable[[str], None]

class ModelClient(Protocol):
    def complete(self, messages, tools, on_text_delta: TextDeltaSink | None = None) -> AssistantTurn:
        raise NotImplementedError
```

In `DeepSeekClient.complete`, keep the existing retry loop and replace the non-streaming conversion with this minimal content path:

```python
stream = self._client.chat.completions.create(
    model=self._model,
    messages=messages,
    tools=tools,
    tool_choice="auto",
    stream=True,
    extra_body={"thinking": {"type": "disabled"}},
)
parts: list[str] = []
for chunk in stream:
    if not chunk.choices:
        raise ModelProtocolError()
    content = getattr(chunk.choices[0].delta, "content", None)
    if content:
        parts.append(content)
        if on_text_delta is not None:
            on_text_delta(content)
return AssistantTurn("".join(parts) or None)
```

- [ ] **Step 4: Run provider tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_provider.py -v
```

Expected: all provider tests PASS after existing non-streaming fixtures are converted to chunk iterators.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/agent/types.py backend/app/agent/provider.py backend/tests/agent/test_provider.py
git commit -m "feat(agent): stream visible model content"
```

---

### Task 2: Reconstruct tool calls and make retries partial-output-safe

**Files:**
- Modify: `backend/app/agent/provider.py`
- Modify: `backend/tests/agent/test_provider.py`

**Interfaces:**
- Consumes: fragments grouped by `tool_call.index` with optional ID, name, and argument strings.
- Produces: existing `ToolCall(id, name, arguments_json)` values in index order.
- Preserves: safe provider messages and three total attempts only before visible output.

- [ ] **Step 1: Write failing fragmented-tool test**

```python
def _tool_chunk(index, *, call_id=None, name=None, arguments=None):
    function = SimpleNamespace(name=name, arguments=arguments)
    fragment = SimpleNamespace(index=index, id=call_id, function=function)
    delta = SimpleNamespace(content=None, reasoning_content="private", tool_calls=[fragment])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def test_complete_reconstructs_tools_without_emitting_reasoning():
    stream = iter([
        _tool_chunk(0, call_id="call_1", name="write_file", arguments='{"pa'),
        _tool_chunk(0, arguments='th":"a.txt","content":"ok"}'),
    ])
    deltas = []
    turn = DeepSeekClient(_client_returning(stream), "deepseek-v4-flash").complete(
        [], [], on_text_delta=deltas.append
    )
    assert deltas == []
    assert turn.tool_calls == (
        ToolCall("call_1", "write_file", '{"path":"a.txt","content":"ok"}'),
    )
```

Also add a stream ending without tool ID/name; expect `ModelProtocolError`.

- [ ] **Step 2: Write failing partial-output retry test**

```python
def test_complete_does_not_retry_after_visible_output(monkeypatch):
    calls = 0
    def create(**kwargs):
        nonlocal calls
        calls += 1
        def broken_stream():
            yield _content_chunk("partial")
            raise _connection_error()
        return broken_stream()

    monkeypatch.setattr(provider.time, "sleep", lambda delay: None)
    deltas = []
    with pytest.raises(ModelProviderError) as captured:
        DeepSeekClient(_client_with_create(create), "deepseek-v4-flash").complete(
            [], [], on_text_delta=deltas.append
        )
    assert deltas == ["partial"]
    assert calls == 1
    assert captured.value.code == "provider_unavailable"
```

- [ ] **Step 3: Run provider tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_provider.py -v
```

Expected: FAIL for missing indexed tool assembly and retry gating.

- [ ] **Step 4: Implement the minimal accumulator and delivery gate**

```python
@dataclass
class _ToolCallParts:
    id: str = ""
    name: str = ""
    arguments: str = ""
```

Append fragment fields by index and validate `id` plus `name` before producing `ToolCall`. Ignore reasoning fields. The core assembly is:

```python
tool_parts: dict[int, _ToolCallParts] = {}
for fragment in getattr(delta, "tool_calls", None) or ():
    current = tool_parts.setdefault(fragment.index, _ToolCallParts())
    if fragment.id:
        current.id += fragment.id
    function = getattr(fragment, "function", None)
    if function is not None:
        if function.name:
            current.name += function.name
        if function.arguments:
            current.arguments += function.arguments

tool_calls = []
for index in sorted(tool_parts):
    current = tool_parts[index]
    if not current.id or not current.name:
        raise ModelProtocolError()
    tool_calls.append(ToolCall(current.id, current.name, current.arguments))
```

Record delivery before invoking the text sink, and gate the existing transient-error branch:

```python
delivered_text = False

def emit(delta: str) -> None:
    nonlocal delivered_text
    delivered_text = True
    if on_text_delta is not None:
        on_text_delta(delta)

except (RateLimitError, APITimeoutError, APIConnectionError) as error:
    if delivered_text:
        raise self._provider_error(error) from error
    if self._retry_or_raise(error, attempt):
        continue
```

The `try` must cover both stream creation and iteration so mid-stream exceptions reach this branch.

- [ ] **Step 5: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_provider.py -v
git add backend/app/agent/provider.py backend/tests/agent/test_provider.py
git commit -m "feat(agent): assemble streamed tool calls"
```

Expected: all provider tests PASS, with three attempts before output and one after partial output.

---

### Task 3: Publish assistant lifecycle events from the Agent loop

**Files:**
- Modify: `backend/app/agent/loop.py`
- Modify: `backend/tests/agent/fakes.py`
- Modify: `backend/tests/agent/test_loop.py`
- Modify: `backend/tests/agent/test_run_manager.py`
- Modify: `backend/tests/test_runs.py`
- Modify: `backend/tests/test_run_events.py`

**Interfaces:**
- Consumes: callback-aware `ModelClient.complete` from Task 1.
- Produces: `assistant.started` with `{}`, `assistant.delta` with `{"delta": str}`, and `assistant.finished` with `{}`.
- Preserves: `message.created` after commit and exactly-once assistant persistence.

- [ ] **Step 1: Update deterministic test models for the callback contract**

Use this behavior in `ScriptedModelClient`; update every local `complete` method in the listed tests to accept the same optional keyword:

```python
def complete(self, messages, tools, on_text_delta=None) -> AssistantTurn:
    self.calls.append({"messages": list(messages), "tools": tools})
    if not self._scripted_turns:
        raise AssertionError("Unexpected model call")
    next_turn = self._scripted_turns.popleft()
    if isinstance(next_turn, BaseException):
        raise next_turn
    if on_text_delta is not None and next_turn.content:
        on_text_delta(next_turn.content)
    return next_turn
```

- [ ] **Step 2: Write the failing lifecycle-order assertion**

Update `test_loop_emits_committed_execution_events` to expect:

```python
assert [event.type for event in events] == [
    "message.created",
    "assistant.started",
    "message.created",
    "assistant.finished",
    "tool.started",
    "tool.finished",
    "message.created",
    "assistant.started",
    "assistant.delta",
    "message.created",
    "assistant.finished",
    "files.changed",
    "run.finished",
]
assert [event.data for event in events if event.type == "assistant.delta"] == [
    {"delta": "Done."}
]
```

- [ ] **Step 3: Run the focused test and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_loop.py::test_loop_emits_committed_execution_events -v
```

Expected: FAIL because assistant lifecycle events are absent.

- [ ] **Step 4: Emit lifecycle events around the provider call**

In `AgentLoop._run`, use the exact ordering:

```python
self._emit("assistant.started", run_id, {})
turn = self._model.complete(
    messages,
    self._registry.schemas(),
    on_text_delta=lambda delta: self._emit(
        "assistant.delta", run_id, {"delta": delta}
    ),
)
self._persist_assistant(run_id, session_id, turn)
self._emit("assistant.finished", run_id, {})
```

Leave exception handling outside persistence. A provider failure emits no `assistant.finished`; the terminal Run reconciliation clears the draft.

- [ ] **Step 5: Add WebSocket integration coverage**

In `test_run_events.py`, use a blocking model that calls `on_text_delta("Hel")`, then `on_text_delta("lo")`, then returns `AssistantTurn("Hello")`. Collect frames through `run.finished` and assert:

```python
lifecycle = [event for event in events if event["type"].startswith("assistant.")]
assert [(event["type"], event["data"]) for event in lifecycle] == [
    ("assistant.started", {}),
    ("assistant.delta", {"delta": "Hel"}),
    ("assistant.delta", {"delta": "lo"}),
    ("assistant.finished", {}),
]
assistant_messages = [
    event for event in events
    if event["type"] == "message.created" and event["data"]["role"] == "assistant"
]
assert len(assistant_messages) == 1
assert assistant_messages[0]["data"]["content"] == "Hello"
```

Retain the completed terminal-state assertion and assert the assistant message precedes `assistant.finished` by comparing their indices in `events`.

- [ ] **Step 6: Run the backend suite and commit**

```powershell
$env:DEEPSEEK_API_KEY=''
.\.venv\Scripts\python.exe -m pytest backend/tests -q
git add backend/app/agent/loop.py backend/tests/agent/fakes.py backend/tests/agent/test_loop.py backend/tests/agent/test_run_manager.py backend/tests/test_runs.py backend/tests/test_run_events.py
git commit -m "feat(agent): publish assistant stream events"
```

Expected: offline backend suite PASS; only environment/capability-dependent tests are skipped.

---

### Task 4: Accumulate transient assistant drafts in Pinia

**Files:**
- Modify: `frontend/src/types/run.ts`
- Modify: `frontend/src/stores/runs.ts`
- Modify: `frontend/src/stores/runs.spec.ts`

**Interfaces:**
- Consumes: assistant lifecycle WebSocket events from Task 3.
- Produces: `AssistantDraft { text: string; active: boolean }`, `draft_by_run`, and `selected_draft`.
- Validates: `assistant.delta.data` is an object with a string `delta`.

- [ ] **Step 1: Add the event and draft types**

Extend `RunEventType` with `assistant.started`, `assistant.delta`, and `assistant.finished`, then add:

```typescript
export interface AssistantDeltaData {
  delta: string
}

export interface AssistantDraft {
  text: string
  active: boolean
}
```

- [ ] **Step 2: Write the failing lifecycle test**

```typescript
test('accumulates assistant deltas until terminal reconciliation', async () => {
  api.createRun.mockResolvedValue(running)
  api.getRun.mockResolvedValue({
    ...running, status: 'completed', final_response: 'Hello world',
  })
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })

  handlers.onEvent(event('assistant.started', {}))
  handlers.onEvent(event('assistant.delta', { delta: 'Hello' }))
  handlers.onEvent(event('assistant.delta', { delta: ' world' }))
  handlers.onEvent(event('assistant.finished', {}))
  expect(store.selected_draft).toEqual({ text: 'Hello world', active: false })

  handlers.onEvent(event('run.finished', {}))
  await vi.waitFor(() => expect(store.selected_draft).toBeNull())
  expect(store.selected?.final_response).toBe('Hello world')
})
```

Add separate tests proving that `message.created` does not cause a blank flash, while a new `assistant.started`, `run.snapshot`, terminal failed/cancelled reconciliation, Run selection change, and explicit `disconnect()` clear stale drafts. A malformed `{ delta: 42 }` must leave draft text unchanged and set `实时事件格式无效`.

- [ ] **Step 3: Run store tests and verify RED**

```powershell
Set-Location frontend
npm test -- src/stores/runs.spec.ts
```

Expected: FAIL because assistant event types and draft state do not exist.

- [ ] **Step 4: Implement minimal draft state**

Add:

```typescript
draft_by_run: {} as Record<string, AssistantDraft>,

selected_draft(state): AssistantDraft | null {
  return state.selected_id ? state.draft_by_run[state.selected_id] ?? null : null
},
```

Handle events before durable message branches:

```typescript
if (event.type === 'assistant.started') {
  this.draft_by_run[event.run_id] = { text: '', active: true }
} else if (event.type === 'assistant.delta') {
  const data = event.data as Partial<AssistantDeltaData> | null
  if (!data || typeof data.delta !== 'string') {
    this.error = '实时事件格式无效'
    return
  }
  const draft = this.draft_by_run[event.run_id] ?? { text: '', active: true }
  this.draft_by_run[event.run_id] = { text: draft.text + data.delta, active: true }
} else if (event.type === 'assistant.finished') {
  const draft = this.draft_by_run[event.run_id]
  if (draft) this.draft_by_run[event.run_id] = { ...draft, active: false }
}
```

Delete the Run draft on authoritative snapshot/reconciliation, selection cleanup, and explicit disconnect. Do not erase it in the transient `onClose` callback before reconnect receives its snapshot.

- [ ] **Step 5: Verify and commit**

```powershell
npm test -- src/stores/runs.spec.ts
git add src/types/run.ts src/stores/runs.ts src/stores/runs.spec.ts
git commit -m "feat(frontend): track assistant stream drafts"
```

Expected: store tests PASS with exact ordering, cleanup, and malformed-payload behavior.

---

### Task 5: Render the live draft in the Vue timeline

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/components/Timeline.vue`
- Modify: `frontend/src/components/Timeline.spec.ts`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `runs.selected_draft` from Task 4.
- Adds Timeline props: `draft: string` and `streaming: boolean`.
- Renders: durable `selected.final_response` when present; otherwise the transient draft.

- [ ] **Step 1: Write failing live-render tests**

```typescript
test('renders a live assistant draft with generation status', () => {
  render(Timeline, { props: {
    title: 'Demo', history: [running], selected: running,
    draft: 'Working on it', streaming: true, cancelling: false, error: null,
  } })
  expect(screen.getByText('Working on it')).toBeTruthy()
  expect(screen.getByText('正在生成')).toBeTruthy()
})

test('prefers the durable final response over a stale draft', () => {
  const completed = { ...running, status: 'completed', final_response: 'Saved answer' }
  render(Timeline, { props: {
    title: 'Demo', history: [completed], selected: completed,
    draft: 'Stale draft', streaming: false, cancelling: false, error: null,
  } })
  expect(screen.getByText('Saved answer')).toBeTruthy()
  expect(screen.queryByText('Stale draft')).toBeNull()
})
```

Use a complete literal `Run` fixture; do not cast a partial object.

- [ ] **Step 2: Run the component test and verify RED**

```powershell
npm test -- src/components/Timeline.spec.ts
```

Expected: FAIL because Timeline has no draft/streaming props or live message.

- [ ] **Step 3: Wire and render one assistant bubble**

Pass from `App.vue`:

```vue
:draft="runs.selected_draft?.text ?? ''"
:streaming="runs.selected_draft?.active ?? false"
```

Add props and replace the final-only article in `Timeline.vue`:

```vue
<article
  v-if="selected.final_response || draft"
  class="message assistant-message"
  :class="{ streaming }"
>
  <span>Agent</span>
  <p>{{ selected.final_response || draft }}</p>
  <small v-if="streaming" class="streaming-status">正在生成</small>
</article>
```

- [ ] **Step 4: Add cursor styling**

```css
.assistant-message.streaming p::after {
  content: "";
  display: inline-block;
  width: .5em;
  height: 1em;
  margin-left: .25em;
  vertical-align: -.12em;
  background: var(--signal);
  animation: stream-cursor 900ms steps(1) infinite;
}
.streaming-status { color: var(--muted); font-size: 10px; letter-spacing: .08em; }
@keyframes stream-cursor { 50% { opacity: 0; } }
```

The existing `prefers-reduced-motion` rule disables the animation.

- [ ] **Step 5: Verify and commit**

```powershell
npm test
npm run build
git add src/App.vue src/components/Timeline.vue src/components/Timeline.spec.ts src/styles.css
git commit -m "feat(frontend): render streaming assistant text"
```

Expected: all frontend tests PASS and production build exits 0.

---

### Task 6: Document and verify the complete path

**Files:**
- Modify: `README.md`
- Test: `backend/tests`
- Test: `frontend` suite and build

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: documented runtime semantics and final evidence that transient output converges to durable state.

- [ ] **Step 1: Update README runtime semantics**

Add this content to the Run API section while retaining one-worker and unsandboxed-command warnings:

```markdown
Visible assistant text is streamed through `assistant.started`,
`assistant.delta`, and `assistant.finished` WebSocket events. Deltas are
transient UI state; complete assistant messages and terminal Run state remain
the durable SQLite record. Tool-call arguments and model reasoning are never
streamed to the interface.
```

- [ ] **Step 2: Run full backend verification**

```powershell
Set-Location F:\Codes\agent
$env:DEEPSEEK_API_KEY=''
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

Expected: all offline tests PASS; only environment/capability-dependent tests are skipped.

- [ ] **Step 3: Run full frontend verification**

```powershell
Set-Location F:\Codes\agent\frontend
npm test
npm run build
```

Expected: all frontend tests PASS and Vite reports a successful production build.

- [ ] **Step 4: Verify the WebSocket event path**

Run the deterministic WebSocket integration test and inspect its literal order:

```powershell
Set-Location F:\Codes\agent
.\.venv\Scripts\python.exe -m pytest backend/tests/test_run_events.py -v
```

Expected lifecycle:

```text
run.snapshot
assistant.started
assistant.delta (one or more for visible text)
message.created
assistant.finished
run.finished
```

If a newly rotated key is locally configured, optionally confirm in the browser that text grows before `run.finished`, no reasoning/tool arguments appear, and `GET /api/runs/{run_id}` ends with the same `final_response`. Otherwise do not run the online smoke test and never reuse the exposed credential.

- [ ] **Step 5: Inspect and commit documentation**

```powershell
git diff --check
git status --short
git diff --stat main...HEAD
git add README.md
git commit -m "docs: explain streaming run output"
```

Expected: no whitespace errors or untracked implementation files; only streaming-feature files differ from `main`.

- [ ] **Step 6: Run final post-commit verification**

```powershell
$env:DEEPSEEK_API_KEY=''
.\.venv\Scripts\python.exe -m pytest backend/tests -q
Set-Location frontend
npm test
npm run build
Set-Location ..
git status --short --branch
```

Expected: backend tests, frontend tests, and build all PASS; the branch is clean and ahead of `main` only by the WebSocket prerequisite, design/plan documents, and streaming implementation commits.
