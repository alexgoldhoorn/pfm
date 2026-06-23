# AI Chat: Persistent Threads & Context Handoff

**Date:** 2026-06-22  
**Tickets:** `[feat] AI chat threads` · `[feat] AI chat context from other screens`  
**Status:** Approved for implementation

---

## Overview

Two related improvements to the AI chat page:

1. **Persistent threads** — chat sessions survive page navigation and are listed in a sidebar so users can return to previous conversations.
2. **Context handoff** — Research, and Portfolio Health gain a "Chat about this" button that opens a named thread with a rich opening message pre-loaded from on-screen data.

These compose: a context handoff always creates a new named thread, which then appears in the thread list.

---

## Database — v24 migration

New table `chat_sessions`:

```sql
CREATE TABLE chat_sessions (
    id               TEXT PRIMARY KEY,       -- random ID, used as session key
    name             TEXT NOT NULL,          -- e.g. "Research: AAPL", "New Chat 1"
    created_at       TEXT NOT NULL,          -- ISO datetime
    last_message_at  TEXT NOT NULL,          -- ISO datetime, updated on each message
    message_count    INTEGER DEFAULT 0,
    messages         TEXT DEFAULT '[]'       -- JSON array of {role, content}
);
```

- `messages` replaces the kv_cache `chat:session:{id}` entries. `_get_history()` and `_append_history()` in `llm.py` read/write this column directly.
- kv_cache is no longer used for chat history.
- New `Database` methods: `create_chat_session(id, name)`, `list_chat_sessions()` (DESC `last_message_at`), `update_chat_session_activity(id, messages)`, `delete_chat_session(id)`.
- `DATABASE_VERSION` bumped to 24. Fresh-install path in `_create_all_tables` + migration path in `_migrate_to_v24`.

---

## Backend API

Four new endpoints under `/api/v1/llm/chat/`:

| Method   | Path                        | Purpose                                      |
|----------|-----------------------------|----------------------------------------------|
| `GET`    | `/sessions`                 | List sessions, ordered by `last_message_at` DESC |
| `POST`   | `/sessions`                 | Create session `{name}` → returns `{id, name}` |
| `DELETE` | `/sessions/{id}`            | Delete session row + messages                |
| `GET`    | `/sessions/{id}/messages`   | Return full messages JSON for a session      |

Existing `POST /api/v1/llm/chat`: if `session_id` is absent or refers to an unknown session, auto-creates a session named `"New Chat"`. `_append_history()` updated to write to the DB column and update `last_message_at` + `message_count`.

---

## Frontend — Chat page

### Layout

The chat page gains a sessions sidebar (left column, collapsible on mobile):

```
┌─────────────────┬──────────────────────────────────┐
│  Threads        │  Research: AAPL            [×]   │
│  ─────────────  │  ──────────────────────────────  │
│  Research: AAPL │  [assistant] I'm looking at...   │
│  Portfolio Hlt  │  [user] What's the fair value?   │
│  New Chat 1     │  [assistant] Based on a P/E...   │
│  ─────────────  │                                  │
│  [+ New Chat]   │  ┌──────────────────────────┐   │
│                 │  │ textarea                  │   │
│                 │  │         [Extract] [Send]  │   │
│                 │  └──────────────────────────┘   │
└─────────────────┴──────────────────────────────────┘
```

### Behaviour

- On load: fetch `GET /sessions`. If none, auto-create `"New Chat 1"` via `POST /sessions`.
- Activate most recent session: load its messages via `GET /sessions/{id}/messages`, render them.
- `sessionId` (`let`) updates whenever the active thread changes.
- Clicking a thread in the sidebar: load its messages, update active highlight.
- **"+ New Chat"** button: `POST /sessions` with name `"New Chat N"` where N = current session list length + 1, activate it.
- **Delete (×)** on a thread: `DELETE /sessions/{id}`. If it was active, activate the next session (or create a new one if list is now empty).
- `window._chatPendingContext` check runs **after** sessions are loaded. If set: call `POST /sessions` with `threadName`, activate new session, send `openingMessage` automatically, clear the global.

### HTML changes (`index.html`)

- Chat page outer div restructured to two-column layout: `col-md-3` sidebar + `col-md-9` message area.
- Sidebar: `<div id="chatSessionsList">` with one `<button>` per session + delete icon. "New Chat" button at bottom.
- The `col-xl-9` centering constraint on the message area is removed (sidebar takes that space).

---

## Frontend — Context handoff

### Shared helper in `pfm_core.js`

```js
function openChatWithContext(threadName, openingMessage) {
    window._chatPendingContext = { threadName, openingMessage };
    window.navigationManager.showPage('chat');
}
```

### Research workbench (`pfm_features.js`)

Button added next to Generate/Save in the research panel. Fires when a symbol is loaded with data.

Opening message includes: symbol, name, current price, fair value estimate, conviction level, upside %, BUY/HOLD/SELL rating, key risks, key catalysts, and the full LLM analysis text if available.

Thread name: `"Research: {SYMBOL}"`

### Portfolio Health panel (`pfm_features.js` / `pfm_analytics.js`)

"Discuss with AI" button in the panel header. Fires after analysis loads successfully.

Opening message includes: all 5 category scores with their reasons (diversification, risk-adjusted return, income, fees, tax efficiency), all recommendations, and the summary text.

Thread name: `"Portfolio Health Analysis"`

---

## Error handling

- Sessions sidebar load failure: show a muted "Could not load threads" message; chat still works with a fresh anonymous session.
- `_chatPendingContext` with a failed session creation: fall back to a new anonymous session, still send the opening message.
- Deleted session that was active: switch to most recent remaining session or create new one.

---

## Testing

- New `Database` method unit tests in `tests/test_database.py` (create/list/update/delete session, `DATABASE_VERSION == 24`).
- New API endpoint tests in `tests/unit/` covering list/create/delete sessions and the auto-create-on-unknown-session behaviour.
- JS smoke test in `web_client/js/tests/` covers `openChatWithContext` existence and `_chatPendingContext` round-trip.
