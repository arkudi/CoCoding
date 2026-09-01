# Streaming Assistant Output Design

## Goal

CoCoding will display user-visible assistant text as DeepSeek produces it while preserving the existing durable Run record, tool-call timeline, cancellation behavior, and WebSocket reconciliation model.

The feature streams only answer text intended for the user. It does not expose model reasoning content or partial tool-call arguments.

## Scope

Included:

- Request streaming from the OpenAI-compatible DeepSeek chat-completions API.
- Reconstruct one complete assistant turn from content and tool-call deltas.
- Publish transient assistant-text events through the existing Run WebSocket.
- Render an in-memory draft in the selected Run timeline.
- Persist the completed assistant message and final Run exactly once.
- Recover cleanly from WebSocket reconnects, cancellation, provider errors, and tool-using turns.

Excluded:

- Displaying chain-of-thought or DeepSeek reasoning content.
- Persisting each token or text delta.
- Streaming command stdout or file diffs.
- Supporting multiple simultaneous Runs or multiple backend workers.
- Replacing the existing WebSocket transport with SSE.

## Architecture

The existing WebSocket remains the single real-time transport. The provider converts the SDK stream into a small callback-driven contract: visible text deltas are reported immediately while content and tool-call fragments are accumulated into the existing `AssistantTurn` result.

The Agent loop owns event semantics. It emits assistant lifecycle events around each model turn and continues to persist only complete messages. The event hub and WebSocket endpoint remain generic fan-out components.

The frontend stores the active assistant draft separately from durable Run details. Delta events append to that draft. The draft remains visible after its message is committed and is cleared only when the next assistant turn begins or authoritative Run reconciliation supplies the final response. This avoids a blank flash between the last delta and `run.finished`.

```text
DeepSeek streaming chunks
          |
          v
DeepSeekClient.complete(on_text_delta)
          |-- accumulate content and tool-call fragments
          |-- callback(delta) -------------------------+
          v                                           |
complete AssistantTurn                                |
          |                                           |
          v                                           v
AgentLoop persists message                 assistant.delta event
          |                                           |
          v                                           v
SQLite durable state                     WebSocket -> Pinia draft
          |                                           |
          +---------------- reconciliation -----------+
```

## Provider Contract

`ModelClient.complete` gains an optional text-delta callback. Test clients may ignore it, keeping deterministic unit tests simple. `DeepSeekClient` calls `chat.completions.create(..., stream=True)` and iterates the returned chunks.

For each choice delta:

- Non-empty `content` is appended to the full content buffer and passed to the callback unchanged.
- Tool-call fragments are grouped by their streamed index.
- Tool-call IDs, function names, and JSON argument fragments are accumulated in arrival order.
- Reasoning-specific fields are ignored.
- Empty chunks and finish markers produce no text event.

At stream completion, the provider returns the same `AssistantTurn` abstraction used today. Missing choices, incomplete tool-call identity, or malformed tool-call metadata produce a safe `ModelProtocolError`. Existing retry behavior applies only before any user-visible delta has been delivered; retrying after partial output could duplicate text, so a mid-stream failure terminates the turn instead.

## Event Protocol

Three Run event types are added:

- `assistant.started`: carries `{}` and begins a new transient draft for a model turn.
- `assistant.delta`: carries `{ "delta": "new text" }` containing only newly produced visible text.
- `assistant.finished`: carries `{}` and marks the draft inactive after the complete assistant message has been committed.

The ordering for a text-only turn is:

```text
assistant.started
assistant.delta (zero or more)
message.created
assistant.finished
run.finished
```

For a tool-using turn, `assistant.finished` occurs before the existing tool events. If a turn contains no visible content, the start and finish events are still valid but the UI need not render an empty bubble.

Events are live projections, not durable records. The authoritative state remains SQLite and `run.snapshot`/`GET /api/runs/{id}`. Queue overflow continues to emit `run.resync_required`.

## Backend Flow

For each model turn, `AgentLoop` emits `assistant.started`, passes a callback to the provider, and converts each callback into `assistant.delta`. Once the provider returns, the loop persists the complete assistant message, emits the existing `message.created`, and emits `assistant.finished`.

If the provider fails after a draft starts, normal Run failure handling persists the terminal error and emits `run.finished`. The frontend clears the draft during reconciliation. Cooperative cancellation remains bounded by the active provider request because the SDK stream is consumed synchronously in the background worker; cancellation is checked at the existing safe boundaries.

The event hub queue remains bounded. Text deltas may increase event frequency, so the implementation may coalesce adjacent text received in the same provider chunk, but it will not add timers or artificial typing delays.

## Frontend State and Rendering

The Runs store adds transient state scoped by Run ID:

- current draft text;
- whether an assistant turn is actively streaming.

Event behavior:

- `assistant.started` replaces any stale draft with an empty active draft.
- `assistant.delta` appends its validated string payload.
- `assistant.finished` marks streaming inactive but keeps text until durable state arrives.
- `message.created` updates durable message state without immediately hiding the draft.
- A following `assistant.started`, terminal reconciliation, Run selection change, or disconnect cleanup removes a stale draft.
- Malformed delta payloads set the existing connection error rather than corrupting state.

`Timeline` renders a temporary Agent message when the selected Run has non-empty draft text. A subtle cursor indicates active generation. The persisted final response replaces the draft after reconciliation, so duplicate text is never shown.

## Reconnection and Consistency

Because drafts are intentionally transient, a reconnect does not replay earlier deltas. On connection, the server sends its durable snapshot. The frontend discards any stale draft and renders the latest committed messages. New deltas received after the snapshot build a new draft.

If disconnection happens during the final answer, the existing reconnect policy attempts to restore the socket. The eventual `run.finished` event or a manual/history reload reconciles the final persisted response. Correct final state takes priority over preserving every animation frame.

## Error Handling

- Authentication, rate-limit, timeout, and provider failures retain their existing safe user messages.
- A failure before the first delta may follow the existing bounded retry policy.
- A failure after any delta is not retried within that turn, avoiding duplicated output.
- Invalid streamed tool-call fragments fail the Run safely and never execute a partially reconstructed tool call.
- WebSocket queue overflow triggers durable resynchronization.
- UI drafts are cleared on terminal failure, cancellation, interruption, Run switching, or authoritative snapshot replacement.

## Testing

Backend tests will cover:

- Visible content chunks are emitted in order and reconstructed exactly.
- Tool-call IDs, names, and JSON argument fragments are reconstructed across chunks.
- Reasoning fields are not emitted.
- Empty and malformed streams fail safely.
- Retries occur before output but not after partial output.
- Agent event ordering for text-only and tool-using turns.
- WebSocket serialization of the three new event types.

Frontend tests will cover:

- Event type parsing and draft accumulation.
- Draft reset on a new turn, Run switch, snapshot, terminal reconciliation, and malformed data.
- Timeline rendering, cursor state, and replacement by final output.
- Existing tool-call, history, cancellation, and workspace behavior remains intact.

Verification will include the complete backend and frontend suites, a production frontend build, a local WebSocket integration check, and an optional DeepSeek smoke test only when a newly rotated key is configured.

## Operational Constraints

The application continues to require one backend worker because the Run manager and event hub are process-local. Streaming does not change the command-execution security boundary: agent commands are unsandboxed and must only run against trusted local workspaces and prompts.
