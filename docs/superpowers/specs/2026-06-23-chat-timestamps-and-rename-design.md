# Design: Chat Message Timestamps & Thread Rename

**Date:** 2026-06-23
**Tickets:** `6gwg9gm52mf28ccR` (message timestamps), `6gwg9mjvpRj7pRpR` (thread timestamp + rename)

## Overview

Two small UX improvements to the AI chat page:
1. Each message bubble shows the time it was sent.
2. Each thread in the sidebar shows its last-active time and can be renamed inline.

The `chat_sessions` table was cleared before this feature was implemented, so all messages going forward will have timestamps. No migration or fallback handling needed.

## Backend Changes

### 1. Add `ts` to stored messages (`portf_server/routers/llm.py`)

`_append_history` currently stores `{"role": ..., "content": ...}`. Change it to include a wall-clock timestamp:

```python
history.append({"role": role, "content": content, "ts": datetime.utcnow().isoformat()})
```

No DB schema change — messages are stored as a JSON blob.

### 2. Rename endpoint (`portf_server/routers/llm.py`)

New route: `PATCH /api/v1/llm/chat/sessions/{session_id}` with body `{"name": "..."}`.
- Validates name is non-empty (strip whitespace).
- Calls `db.rename_chat_session(session_id, name)`.
- Returns `{"id": session_id, "name": name}` or 404.

### 3. `rename_chat_session` DB method (`portf_manager/database.py`)

```python
def rename_chat_session(self, id: str, name: str) -> bool:
    # UPDATE chat_sessions SET name=? WHERE id=?
    # Returns True if a row was updated
```

## Frontend Changes (`web_client/js/pfm_features.js`)

### 1. `appendMessage(role, text, ts=null)`

Add optional `ts` parameter. Render a small timestamp below the bubble:

```
HH:MM          — if ts is today
DD MMM HH:MM   — if ts is an older date
(nothing)      — if ts is null (defensive fallback)
```

Timestamp styled as `<div class="text-muted" style="font-size:0.7rem;">HH:MM</div>` aligned to the same side as the bubble (right for user, left for assistant).

### 2. Load history with timestamps

In `activateSession`, when calling `appendMessage(m.role, m.content)` for each history item, pass `m.ts` as the third argument.

### 3. New message: capture `ts` before sending

When the user sends a message, capture `const ts = new Date().toISOString()` and pass it to `appendMessage('user', text, ts)`. The assistant response timestamp comes from the server response (or falls back to `new Date().toISOString()` on arrival).

### 4. Thread sidebar: show `last_message_at`

In `renderSessionsList`, add a small muted line below the thread name showing the formatted `last_message_at` (same `HH:MM` / `DD MMM HH:MM` logic):

```html
<span class="text-truncate flex-grow-1">${esc(s.name)}</span>
<span class="text-muted d-block" style="font-size:0.7rem;">${fmtTs(s.last_message_at)}</span>
```

### 5. Thread rename

Each thread item gets a pencil icon (`bi-pencil`) beside the name. On click:
- Replace the name `<span>` with an `<input type="text">` pre-filled with the current name.
- On `blur` or `Enter`: PATCH the name, update the local `sessions` array, re-render.
- On `Escape`: cancel, re-render without changes.
- Empty name after trim → cancel silently.

### 6. `apiClient.renameChatSession(id, name)` (`pfm_core.js`)

```js
async renameChatSession(id, name) {
    const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions/' + id, {
        method: 'PATCH',
        headers: { ...this.headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
    if (!response.ok) throw new Error('Failed to rename session');
    return response.json();
}
```

## Shared Utility

A module-scoped `fmtTs(isoString)` helper formats an ISO timestamp for display. Used by both the message bubbles and the thread sidebar. Returns empty string for null/undefined input.

## Testing

- Existing JS unit tests continue to pass (no changes to `Fmt`, `esc`, or table helpers).
- Manual: send a few messages, confirm timestamps appear. Rename a thread, confirm it persists after page reload.
