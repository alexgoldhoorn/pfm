# AI Chat: Persistent Threads & Context Handoff — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent named chat sessions with a sidebar thread list, and "Chat about this" buttons on the Research workbench and Portfolio Health panel that open a named thread pre-loaded with rich on-screen context.

**Architecture:** A new `chat_sessions` DB table (v24 migration) replaces the kv_cache for chat history storage. Four new REST endpoints manage sessions. The chat page gains a two-column layout with a sessions sidebar. A shared `openChatWithContext(threadName, openingMessage)` helper in `pfm_core.js` wires other pages to chat via a JS global `window._chatPendingContext`.

**Tech Stack:** Python/FastAPI (backend), SQLite (database), Vanilla JS + Bootstrap 5 (frontend), pytest (testing)

## Global Constraints

- Python 3.13, formatted with black (line length 88)
- Run tests with: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
- Run a single test: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/path/test.py::TestClass::test_name -v`
- Pre-push hook runs full unit tests — must pass before any push
- `DATABASE_VERSION` in `portf_manager/database.py` must be bumped to 24
- Version assertions in `tests/test_database.py` use `== 23` in 4 places — all must be updated to `== 24`
- After any `web_client/` change: `docker compose build web && docker stop portf_web && docker compose up -d web`
- Co-author line on commits: `Co-Authored-By: Oz <oz-agent@warp.dev>`
- Commit message convention: `feat:` prefix
- Update `PROJECT_STATUS.md` and `CLAUDE.md` in the final commit

---

## File Map

| File | Change |
|------|--------|
| `portf_manager/database.py` | Bump `DATABASE_VERSION` to 24; add `chat_sessions` table to `_create_all_tables`; add `_migrate_to_v24()`; add `create_chat_session`, `get_chat_session`, `list_chat_sessions`, `update_chat_session_activity`, `delete_chat_session` methods |
| `portf_server/routers/llm.py` | Remove kv_cache history helpers; rewrite `_get_history` / `_append_history` to use DB; auto-create session in `process_chat_request`; add 4 session REST endpoints |
| `web_client/index.html` | Restructure chat page to two-column layout (sidebar + message area); add `#chatSessionsList` sidebar; add "Chat about this" button to research workbench; add "Discuss with AI" button to portfolio health result bar |
| `web_client/js/pfm_core.js` | Add `listChatSessions`, `createChatSession`, `deleteChatSession`, `getChatSessionMessages` to `apiClient`; add `window.openChatWithContext` helper |
| `web_client/js/pfm_features.js` | Rewrite `setupChatPage()` with session loading, sidebar rendering, thread switching, pending context handling; add context button wiring in `load()` (research) and `renderResult()` (portfolio health) |
| `tests/test_database.py` | Bump 4 version assertions from 23 → 24; add `TestChatSessions` class |
| `tests/unit/test_chat_sessions.py` | New file — API tests for 4 session endpoints + auto-create behaviour |

---

### Task 1: Database — v24 migration + chat_sessions CRUD

**Files:**
- Modify: `portf_manager/database.py`
- Test: `tests/test_database.py`

**Interfaces:**
- Produces:
  - `db.create_chat_session(id: str, name: str) -> None`
  - `db.get_chat_session(id: str) -> Optional[Dict]` — keys: `id, name, created_at, last_message_at, message_count, messages`
  - `db.list_chat_sessions() -> List[Dict]` — ordered by `last_message_at` DESC
  - `db.update_chat_session_activity(id: str, messages: list) -> None` — updates `messages` JSON, `last_message_at`, and `message_count`
  - `db.delete_chat_session(id: str) -> bool`

- [ ] **Step 1: Write failing tests**

In `tests/test_database.py`, find the class `TestDatabaseMigration` (or the top-level test for version) and update the 4 `== 23` assertions to `== 24`. Also add a new test class at the bottom of the file:

```python
class TestChatSessions:
    def test_create_and_get_session(self, tmp_path):
        from portf_manager.database import Database
        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("sess1", "My Thread")
        s = db.get_chat_session("sess1")
        assert s is not None
        assert s["name"] == "My Thread"
        assert s["message_count"] == 0
        assert s["messages"] == []

    def test_list_sessions_ordered_by_last_message(self, tmp_path):
        from portf_manager.database import Database
        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("a", "Alpha")
        import time; time.sleep(0.01)
        db.create_chat_session("b", "Beta")
        sessions = db.list_chat_sessions()
        assert len(sessions) == 2
        assert sessions[0]["id"] == "b"  # most recent first

    def test_update_session_activity(self, tmp_path):
        from portf_manager.database import Database
        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("s1", "Thread")
        msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        db.update_chat_session_activity("s1", msgs)
        s = db.get_chat_session("s1")
        assert s["messages"] == msgs
        assert s["message_count"] == 2

    def test_delete_session(self, tmp_path):
        from portf_manager.database import Database
        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("s1", "Thread")
        result = db.delete_chat_session("s1")
        assert result is True
        assert db.get_chat_session("s1") is None

    def test_get_nonexistent_session_returns_none(self, tmp_path):
        from portf_manager.database import Database
        db = Database(str(tmp_path / "t.db"))
        assert db.get_chat_session("missing") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_database.py::TestChatSessions -v
```

Expected: FAIL with `AttributeError: 'Database' object has no attribute 'create_chat_session'`

- [ ] **Step 3: Implement DB changes**

In `portf_manager/database.py`:

**3a.** Change line 16:
```python
DATABASE_VERSION = 24
```

**3b.** In `_create_all_tables`, add the new table (after the `push_subscriptions` CREATE block, before `self._set_database_version`):
```python
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id               TEXT PRIMARY KEY,
                name             TEXT NOT NULL,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                last_message_at  TEXT NOT NULL DEFAULT (datetime('now')),
                message_count    INTEGER DEFAULT 0,
                messages         TEXT DEFAULT '[]'
            )
            """
        )
```

**3c.** Add migration method after `_migrate_to_v23`:
```python
    def _migrate_to_v24(self, conn: sqlite3.Connection) -> None:
        """Add chat_sessions table for persistent named chat threads."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id               TEXT PRIMARY KEY,
                name             TEXT NOT NULL,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                last_message_at  TEXT NOT NULL DEFAULT (datetime('now')),
                message_count    INTEGER DEFAULT 0,
                messages         TEXT DEFAULT '[]'
            )
            """
        )
        conn.commit()
```

**3d.** In `_run_migrations`, add after `if current_version < 23`:
```python
        if current_version < 24:
            self._migrate_to_v24(conn)
```

**3e.** Add CRUD methods (add as a new section after the `app_settings` methods, before `get_database`):
```python
    # ── Chat sessions ──────────────────────────────────────────────────────────

    def create_chat_session(self, id: str, name: str) -> None:
        """Create a new named chat session."""
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO chat_sessions (id, name, created_at, last_message_at)
                VALUES (?, ?, datetime('now'), datetime('now'))
                """,
                (id, name),
            )
            conn.commit()

    def get_chat_session(self, id: str) -> Optional[Dict]:
        """Return a chat session by id, or None if not found."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT id, name, created_at, last_message_at, message_count, messages "
                "FROM chat_sessions WHERE id = ?",
                (id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["messages"] = json.loads(result.get("messages") or "[]")
        return result

    def list_chat_sessions(self) -> List[Dict]:
        """Return all chat sessions ordered by last_message_at DESC."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at, last_message_at, message_count "
                "FROM chat_sessions ORDER BY last_message_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_chat_session_activity(self, id: str, messages: list) -> None:
        """Persist messages list and update last_message_at + message_count."""
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE chat_sessions
                SET messages = ?, last_message_at = datetime('now'), message_count = ?
                WHERE id = ?
                """,
                (json.dumps(messages), len(messages), id),
            )
            conn.commit()

    def delete_chat_session(self, id: str) -> bool:
        """Delete a chat session. Returns True if a row was deleted."""
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (id,))
            conn.commit()
        return cursor.rowcount > 0
```

Note: `json` is already imported at the top of `database.py`. If not, add `import json`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_database.py -v
```

Expected: All `TestChatSessions` tests pass; version assertions now expect 24.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/database.py tests/test_database.py
git commit -m "feat: add chat_sessions table (v24 migration) with CRUD methods

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 2: Backend — Session history storage + REST endpoints

**Files:**
- Modify: `portf_server/routers/llm.py`
- Create: `tests/unit/test_chat_sessions.py`

**Interfaces:**
- Consumes: `db.create_chat_session`, `db.get_chat_session`, `db.list_chat_sessions`, `db.update_chat_session_activity`, `db.delete_chat_session` (from Task 1)
- Produces:
  - `GET /api/v1/llm/chat/sessions` → `[{id, name, created_at, last_message_at, message_count}]`
  - `POST /api/v1/llm/chat/sessions` body `{name: str}` → `{id: str, name: str}`
  - `DELETE /api/v1/llm/chat/sessions/{session_id}` → 204 or 404
  - `GET /api/v1/llm/chat/sessions/{session_id}/messages` → `{messages: [{role, content}]}`
  - Existing `POST /api/v1/llm/chat` auto-creates a session named `"New Chat"` when `session_id` is absent or unknown

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_chat_sessions.py`:

```python
"""Tests for chat session REST endpoints."""

import pytest
import uuid
from httpx import AsyncClient


@pytest.mark.asyncio
class TestChatSessionEndpoints:
    async def test_list_sessions_empty(self, async_test_client: AsyncClient, auth_headers):
        response = await async_test_client.get("/api/v1/llm/chat/sessions", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

    async def test_create_session(self, async_test_client: AsyncClient, auth_headers):
        response = await async_test_client.post(
            "/api/v1/llm/chat/sessions",
            json={"name": "Test Thread"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Thread"
        assert "id" in data

    async def test_list_sessions_after_create(self, async_test_client: AsyncClient, auth_headers):
        await async_test_client.post(
            "/api/v1/llm/chat/sessions",
            json={"name": "Alpha"},
            headers=auth_headers,
        )
        response = await async_test_client.get("/api/v1/llm/chat/sessions", headers=auth_headers)
        assert response.status_code == 200
        sessions = response.json()
        assert any(s["name"] == "Alpha" for s in sessions)

    async def test_delete_session(self, async_test_client: AsyncClient, auth_headers):
        create_resp = await async_test_client.post(
            "/api/v1/llm/chat/sessions",
            json={"name": "ToDelete"},
            headers=auth_headers,
        )
        session_id = create_resp.json()["id"]
        del_resp = await async_test_client.delete(
            f"/api/v1/llm/chat/sessions/{session_id}", headers=auth_headers
        )
        assert del_resp.status_code == 204

        list_resp = await async_test_client.get("/api/v1/llm/chat/sessions", headers=auth_headers)
        assert not any(s["id"] == session_id for s in list_resp.json())

    async def test_delete_nonexistent_session_returns_404(self, async_test_client: AsyncClient, auth_headers):
        response = await async_test_client.delete(
            "/api/v1/llm/chat/sessions/nonexistent-id", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_get_session_messages_empty(self, async_test_client: AsyncClient, auth_headers):
        create_resp = await async_test_client.post(
            "/api/v1/llm/chat/sessions",
            json={"name": "Empty"},
            headers=auth_headers,
        )
        session_id = create_resp.json()["id"]
        msg_resp = await async_test_client.get(
            f"/api/v1/llm/chat/sessions/{session_id}/messages", headers=auth_headers
        )
        assert msg_resp.status_code == 200
        assert msg_resp.json() == {"messages": []}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_chat_sessions.py -v
```

Expected: FAIL with 404 (endpoints don't exist yet).

- [ ] **Step 3: Rewrite history helpers in `portf_server/routers/llm.py`**

Remove these lines (lines ~42–70):
```python
_CHAT_HISTORY_TTL = 24 * 3600  # forget idle sessions after a day
_CHAT_HISTORY_MAX = 10  # keep only the last N messages


def _history_key(session_id: str) -> str:
    return f"chat:session:{session_id}"


def _get_history(db, session_id: str) -> List[Dict[str, str]]:
    """Return the stored message history for *session_id* (empty if none)."""
    try:
        return db.cache_get(_history_key(session_id)) or []
    except Exception as e:
        logger.warning(f"chat history read failed for {session_id}: {e}")
        return []


def _append_history(db, session_id: str, role: str, content: str) -> None:
    """Append a message to the session history, trimmed to the last N."""
    history = _get_history(db, session_id)
    history.append({"role": role, "content": content})
    history = history[-_CHAT_HISTORY_MAX:]
    try:
        db.cache_set(_history_key(session_id), history, _CHAT_HISTORY_TTL)
    except Exception as e:
        logger.warning(f"chat history write failed for {session_id}: {e}")
```

Replace with:
```python
_CHAT_HISTORY_MAX = 20  # keep only the last N messages per session


def _get_history(db, session_id: str) -> List[Dict[str, str]]:
    """Return stored message history for session_id from the DB (empty if none)."""
    try:
        session = db.get_chat_session(session_id)
        return session["messages"] if session else []
    except Exception as e:
        logger.warning(f"chat history read failed for {session_id}: {e}")
        return []


def _append_history(db, session_id: str, role: str, content: str) -> None:
    """Append a message to the session history and persist to DB."""
    history = _get_history(db, session_id)
    history.append({"role": role, "content": content})
    history = history[-_CHAT_HISTORY_MAX:]
    try:
        db.update_chat_session_activity(session_id, history)
    except Exception as e:
        logger.warning(f"chat history write failed for {session_id}: {e}")
```

- [ ] **Step 4: Update `process_chat_request` to auto-create session**

First, add `import uuid` at the top of `llm.py` with the other standard library imports (after `import threading`):
```python
import uuid
```

In `EnhancedChatEngine.process_chat_request`, find:
```python
session_id = request.session_id or "default"
```

Replace with:
```python
session_id = request.session_id or uuid.uuid4().hex[:12]
# ensure session row exists (auto-create for API callers without a session)
if not db.get_chat_session(session_id):
    db.create_chat_session(session_id, "New Chat")
```

- [ ] **Step 5: Add the 4 session REST endpoints**

Add before the `@router.post("/extract-transactions")` route (around line 636). Also add `CreateSessionRequest` Pydantic model near the other models at the top:

```python
class CreateSessionRequest(BaseModel):
    name: str
```

Then add the endpoints:
```python
@router.get("/chat/sessions")
def list_chat_sessions(
    db: Database = Depends(get_db),
    api_key_info: dict = Depends(get_api_key_auth_for_llm),
):
    """List all chat sessions ordered by most recent first."""
    return db.list_chat_sessions()


@router.post("/chat/sessions")
def create_chat_session(
    request: CreateSessionRequest,
    db: Database = Depends(get_db),
    api_key_info: dict = Depends(get_api_key_auth_for_llm),
):
    """Create a new named chat session."""
    session_id = uuid.uuid4().hex[:12]
    db.create_chat_session(session_id, request.name)
    return {"id": session_id, "name": request.name}


@router.delete("/chat/sessions/{session_id}", status_code=204)
def delete_chat_session(
    session_id: str,
    db: Database = Depends(get_db),
    api_key_info: dict = Depends(get_api_key_auth_for_llm),
):
    """Delete a chat session and its message history."""
    deleted = db.delete_chat_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/chat/sessions/{session_id}/messages")
def get_chat_session_messages(
    session_id: str,
    db: Database = Depends(get_db),
    api_key_info: dict = Depends(get_api_key_auth_for_llm),
):
    """Return the full message history for a chat session."""
    session = db.get_chat_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"messages": session["messages"]}
```

**Important:** These 4 routes must be registered **before** `@router.post("/chat")` in the file, because FastAPI matches routes in order. The `/chat/sessions` path must not be shadowed by `/chat`. Check that `router` in llm.py is defined with prefix `/llm`, so the full paths are `/api/v1/llm/chat/sessions` — confirm by checking `portf_server/app.py` where the router is included.

- [ ] **Step 6: Run all tests**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_chat_sessions.py tests/test_database.py -v
```

Expected: All passing.

- [ ] **Step 7: Run full suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v
```

Expected: All passing (or same baseline as before).

- [ ] **Step 8: Commit**

```bash
git add portf_server/routers/llm.py tests/unit/test_chat_sessions.py
git commit -m "feat: rewrite chat history to DB and add session REST endpoints

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 3: Frontend — Chat page threads sidebar

**Files:**
- Modify: `web_client/index.html`
- Modify: `web_client/js/pfm_core.js`
- Modify: `web_client/js/pfm_features.js`

**Interfaces:**
- Consumes:
  - `GET /api/v1/llm/chat/sessions`
  - `POST /api/v1/llm/chat/sessions` body `{name}`
  - `DELETE /api/v1/llm/chat/sessions/{id}`
  - `GET /api/v1/llm/chat/sessions/{id}/messages`
  - `window._chatPendingContext` set by Task 4

- [ ] **Step 1: Add session API methods to `pfm_core.js`**

In the `apiClient` object in `pfm_core.js`, add after `sendChat`:

```javascript
        async listChatSessions() {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load sessions');
            return response.json();
        },

        async createChatSession(name) {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ name })
            });
            if (!response.ok) throw new Error('Failed to create session');
            return response.json();
        },

        async deleteChatSession(id) {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions/' + id, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok && response.status !== 404) throw new Error('Failed to delete session');
        },

        async getChatSessionMessages(id) {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions/' + id + '/messages', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load messages');
            return response.json();
        },
```

- [ ] **Step 2: Restructure chat page HTML in `index.html`**

Find the `<!-- Chat Page -->` section (around line 1232). Replace the entire `<div id="chatPage" ...>` block with:

```html
                <!-- Chat Page -->
                <div id="chatPage" class="page-content" style="display: none;">
                    <div class="d-flex align-items-center justify-content-between mb-3">
                        <div>
                            <h4 class="mb-0"><i class="bi bi-robot me-2 text-primary"></i>AI Chat<button class="btn btn-sm btn-link p-0 ms-2 align-baseline" onclick="showPageHelp('chat')" title="What is this page?"><i class="bi bi-question-circle"></i></button></h4>
                            <p class="text-muted small mb-0">Ask questions about your portfolio or extract transactions from broker statements.</p>
                        </div>
                    </div>

                    <div class="row g-3">
                        <!-- Sessions sidebar -->
                        <div class="col-md-3 d-none d-md-block">
                            <div class="card h-100">
                                <div class="card-header py-2 d-flex align-items-center justify-content-between">
                                    <span class="fw-semibold small">Threads</span>
                                    <button class="btn btn-sm btn-outline-primary py-0 px-2" id="chatNewBtn" title="New chat">
                                        <i class="bi bi-plus-lg"></i>
                                    </button>
                                </div>
                                <div id="chatSessionsList" class="list-group list-group-flush overflow-auto" style="max-height:60vh;">
                                    <div class="list-group-item text-muted small py-2">Loading…</div>
                                </div>
                            </div>
                        </div>

                        <!-- Message area -->
                        <div class="col-12 col-md-9">
                            <!-- Active thread name + mobile new-chat -->
                            <div class="d-flex align-items-center gap-2 mb-2">
                                <span id="chatActiveThreadName" class="fw-semibold text-truncate"></span>
                                <button class="btn btn-sm btn-outline-primary d-md-none ms-auto" id="chatNewBtnMobile">
                                    <i class="bi bi-plus-lg me-1"></i>New
                                </button>
                            </div>
                            <!-- Message thread -->
                            <div id="chatMessages" class="border rounded bg-white p-3 mb-3" style="height:clamp(260px,50vh,520px);overflow-y:auto;display:flex;flex-direction:column;gap:12px;">
                                <div class="text-muted text-center small mt-auto" id="chatEmpty">
                                    <i class="bi bi-robot fs-3 d-block mb-2"></i>
                                    <strong>What can I help you with?</strong><br>
                                    <span class="text-muted">Try: "What is my total P&amp;L?" &middot; "Show me my top holdings" &middot; or paste a broker statement and click <strong>Extract &amp; Import</strong>.</span>
                                </div>
                            </div>

                            <!-- Input area -->
                            <div class="card">
                                <div class="card-body pb-2">
                                    <textarea class="form-control mb-2" id="chatInput" rows="3"
                                        placeholder="Ask a question about your portfolio, or paste a broker statement to extract transactions&#10;&#10;Tip: Ctrl+Enter to send quickly."></textarea>
                                    <div class="d-flex gap-2 justify-content-end">
                                        <button class="btn btn-outline-secondary btn-sm" id="chatExtractBtn" title="Extract buy/sell transactions from pasted broker statement">
                                            <i class="bi bi-magic me-1"></i>Extract &amp; Import
                                        </button>
                                        <button class="btn btn-primary btn-sm" id="chatSendBtn" title="Send message (Ctrl+Enter)">
                                            <i class="bi bi-send me-1"></i>Send
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
```

- [ ] **Step 3: Rewrite `setupChatPage()` in `pfm_features.js`**

Replace the entire `setupChatPage` function (lines 476–653) with:

```javascript
function setupChatPage() {
    const messagesEl = document.getElementById('chatMessages');
    const inputEl    = document.getElementById('chatInput');
    const sendBtn    = document.getElementById('chatSendBtn');
    const extractBtn = document.getElementById('chatExtractBtn');
    const newBtn     = document.getElementById('chatNewBtn');
    const newBtnMob  = document.getElementById('chatNewBtnMobile');
    const sessionsList = document.getElementById('chatSessionsList');
    const threadNameEl = document.getElementById('chatActiveThreadName');
    if (!messagesEl || !inputEl) return;

    let sessionId = null;
    let sessions = [];

    function scrollBottom() { messagesEl.scrollTop = messagesEl.scrollHeight; }

    function removeEmpty() {
        const empty = document.getElementById('chatEmpty');
        if (empty) empty.remove();
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    function appendMessage(role, text) {
        removeEmpty();
        const isUser = role === 'user';
        let body, extraStyle;
        if (isUser) {
            body = escapeHtml(text);
            extraStyle = 'white-space:pre-wrap;';
        } else if (window.marked) {
            body = marked.parse(String(text || ''), { breaks: true });
            extraStyle = '';
        } else {
            body = escapeHtml(text);
            extraStyle = 'white-space:pre-wrap;';
        }
        const div = document.createElement('div');
        div.className = `d-flex ${isUser ? 'justify-content-end' : 'justify-content-start'}`;
        div.innerHTML = `
            <div class="px-3 py-2 rounded-3 chat-bubble ${isUser ? 'bg-primary text-white' : 'chat-bubble-assistant border'}"
                 style="max-width:80%;word-break:break-word;${extraStyle}">${body}</div>`;
        messagesEl.appendChild(div);
        scrollBottom();
    }

    function appendTransactionCard(transactions) {
        if (!transactions || transactions.length === 0) return;
        removeEmpty();

        const rows = transactions.map((tx, i) => `
            <tr>
                <td><input class="form-check-input chat-tx-select" type="checkbox" checked data-idx="${i}"></td>
                <td><input type="date" class="form-control form-control-sm chat-tx-date" data-idx="${i}" value="${escapeForAttr(tx.date || '')}"></td>
                <td>
                    <input type="text" class="form-control form-control-sm chat-tx-symbol mb-1" data-idx="${i}" value="${escapeForAttr(tx.symbol || '')}" placeholder="Symbol">
                    <input type="text" class="form-control form-control-sm chat-tx-name" data-idx="${i}" value="${escapeForAttr(tx.asset_name || '')}" placeholder="Name">
                </td>
                <td>
                    <select class="form-select form-select-sm chat-tx-type" data-idx="${i}">
                        ${['buy','sell','dividend','interest'].map(t => `<option value="${t}" ${(tx.tx_type||'') === t ? 'selected' : ''}>${t.toUpperCase()}</option>`).join('')}
                    </select>
                </td>
                <td><input type="number" class="form-control form-control-sm chat-tx-qty" data-idx="${i}" value="${tx.quantity || 0}" step="any" min="0"></td>
                <td>
                    <input type="number" class="form-control form-control-sm chat-tx-price mb-1" data-idx="${i}" value="${tx.price || 0}" step="any" min="0">
                    <input type="text" class="form-control form-control-sm chat-tx-currency" data-idx="${i}" value="${escapeForAttr(tx.currency || 'EUR')}" maxlength="3" style="width:5ch">
                </td>
                <td><input type="number" class="form-control form-control-sm chat-tx-fees" data-idx="${i}" value="${parseFloat(tx.fees)||0}" step="any" min="0"></td>
            </tr>`).join('');

        const card = document.createElement('div');
        card.className = 'align-self-start w-100';
        card.innerHTML = `
            <div class="card border-success">
                <div class="card-header bg-success text-white py-1 small">
                    <i class="bi bi-magic me-1"></i>Found ${transactions.length} transaction(s) — select which to import
                </div>
                <div class="card-body p-2">
                    <div class="table-responsive">
                        <table class="table table-sm mb-2">
                            <thead><tr><th></th><th>Date</th><th>Asset</th><th>Type</th><th>Qty</th><th>Price / Cur</th><th>Fees</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                    <button class="btn btn-success btn-sm chat-import-btn">
                        <i class="bi bi-check-lg me-1"></i>Import selected
                    </button>
                </div>
            </div>`;

        card.querySelector('.chat-import-btn').addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            const checkedIdxs = Array.from(card.querySelectorAll('.chat-tx-select:checked'))
                .map(cb => parseInt(cb.dataset.idx));
            if (checkedIdxs.length === 0) { alert('Nothing selected.'); return; }
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving…';
            const f = (cls, i) => card.querySelector(`.${cls}[data-idx="${i}"]`);
            const normalized = checkedIdxs.map(i => ({
                symbol: f('chat-tx-symbol', i).value,
                name: f('chat-tx-name', i).value || f('chat-tx-symbol', i).value,
                asset_type: 'stock',
                tx_type: f('chat-tx-type', i).value,
                date: f('chat-tx-date', i).value,
                quantity: parseFloat(f('chat-tx-qty', i).value) || 0,
                price: parseFloat(f('chat-tx-price', i).value) || 0,
                currency: (f('chat-tx-currency', i).value || 'EUR').toUpperCase(),
                fees: parseFloat(f('chat-tx-fees', i).value) || 0,
                notes: transactions[i] ? (transactions[i].raw_text || '') : ''
            }));
            try {
                const result = await window.apiClient.saveImportedTransactions(normalized);
                btn.remove();
                appendMessage('assistant', `Imported ${result.saved} transaction(s).${result.errors.length ? '\n' + result.errors.join('\n') : ''}`);
            } catch (err) {
                btn.disabled = false;
                btn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Import selected';
                appendMessage('assistant', 'Save failed: ' + err.message);
            }
        });

        messagesEl.appendChild(card);
        scrollBottom();
    }

    function renderSessionsList() {
        if (!sessions.length) {
            sessionsList.innerHTML = '<div class="list-group-item text-muted small py-2">No threads yet.</div>';
            return;
        }
        sessionsList.innerHTML = sessions.map(s => `
            <button class="list-group-item list-group-item-action d-flex align-items-center gap-2 py-2 px-3 ${s.id === sessionId ? 'active' : ''}"
                    data-session-id="${esc(s.id)}" style="font-size:0.85rem;">
                <span class="text-truncate flex-grow-1">${esc(s.name)}</span>
                <span class="badge bg-secondary rounded-pill ms-auto" style="font-size:0.7rem;">${s.message_count || 0}</span>
                <i class="bi bi-x chat-delete-session flex-shrink-0" title="Delete thread" style="cursor:pointer;opacity:0.6;"></i>
            </button>`).join('');

        sessionsList.querySelectorAll('[data-session-id]').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                if (e.target.classList.contains('chat-delete-session')) {
                    e.stopPropagation();
                    const id = btn.dataset.sessionId;
                    if (!confirm('Delete this thread?')) return;
                    await window.apiClient.deleteChatSession(id);
                    sessions = sessions.filter(s => s.id !== id);
                    if (id === sessionId) {
                        if (sessions.length) {
                            await activateSession(sessions[0].id);
                        } else {
                            await createAndActivateSession('New Chat 1');
                        }
                    } else {
                        renderSessionsList();
                    }
                    return;
                }
                await activateSession(btn.dataset.sessionId);
            });
        });
    }

    async function activateSession(id) {
        sessionId = id;
        const s = sessions.find(x => x.id === id);
        if (threadNameEl) threadNameEl.textContent = s ? s.name : '';
        messagesEl.innerHTML = '<div class="text-muted text-center small mt-auto" id="chatEmpty"><i class="bi bi-robot fs-3 d-block mb-2"></i><strong>What can I help you with?</strong><br><span class="text-muted">Try: "What is my total P&L?" or paste a broker statement.</span></div>';
        renderSessionsList();
        try {
            const { messages } = await window.apiClient.getChatSessionMessages(id);
            if (messages && messages.length) {
                messagesEl.innerHTML = '';
                messages.forEach(m => appendMessage(m.role, m.content));
            }
        } catch (e) { /* leave empty state */ }
    }

    async function createAndActivateSession(name) {
        const s = await window.apiClient.createChatSession(name);
        sessions.unshift({ id: s.id, name: s.name, message_count: 0 });
        await activateSession(s.id);
    }

    async function doSend() {
        const text = inputEl.value.trim();
        if (!text) return;
        inputEl.value = '';
        appendMessage('user', text);
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
            const data = await window.apiClient.sendChat(text, sessionId);
            appendMessage('assistant', data.answer || '(no response)');
            // update message_count in sidebar
            const s = sessions.find(x => x.id === sessionId);
            if (s) { s.message_count = (s.message_count || 0) + 2; renderSessionsList(); }
        } catch (err) {
            appendMessage('assistant', 'Error: ' + err.message);
        } finally {
            sendBtn.disabled = false;
            sendBtn.innerHTML = '<i class="bi bi-send me-1"></i>Send';
        }
    }

    async function doExtract() {
        const text = inputEl.value.trim();
        if (!text) { alert('Paste a broker statement first.'); return; }
        inputEl.value = '';
        appendMessage('user', '[Broker statement — extracting transactions…]');
        extractBtn.disabled = true;
        extractBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
            const data = await window.apiClient.extractTransactions(text);
            if (!data.transactions || data.transactions.length === 0) {
                appendMessage('assistant', 'No transactions could be extracted from that text.');
            } else {
                appendTransactionCard(data.transactions);
            }
        } catch (err) {
            appendMessage('assistant', 'Extraction error: ' + err.message);
        } finally {
            extractBtn.disabled = false;
            extractBtn.innerHTML = '<i class="bi bi-magic me-1"></i>Extract &amp; Import';
        }
    }

    async function doNewChat() {
        const n = sessions.filter(s => s.name.startsWith('New Chat')).length + 1;
        await createAndActivateSession(`New Chat ${n}`);
    }

    sendBtn.addEventListener('click', doSend);
    extractBtn.addEventListener('click', doExtract);
    inputEl.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) doSend(); });
    if (newBtn) newBtn.addEventListener('click', doNewChat);
    if (newBtnMob) newBtnMob.addEventListener('click', doNewChat);

    // Initialise: load sessions, handle pending context
    (async () => {
        try {
            sessions = await window.apiClient.listChatSessions();
        } catch (e) {
            sessionsList.innerHTML = '<div class="list-group-item text-muted small py-2">Could not load threads.</div>';
            sessions = [];
        }

        const pending = window._chatPendingContext;
        window._chatPendingContext = null;

        if (pending) {
            await createAndActivateSession(pending.threadName);
            appendMessage('user', pending.openingMessage);
            sendBtn.disabled = true;
            sendBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
            try {
                const data = await window.apiClient.sendChat(pending.openingMessage, sessionId);
                appendMessage('assistant', data.answer || '(no response)');
                const s = sessions.find(x => x.id === sessionId);
                if (s) { s.message_count = (s.message_count || 0) + 2; renderSessionsList(); }
            } catch (err) {
                appendMessage('assistant', 'Error: ' + err.message);
            } finally {
                sendBtn.disabled = false;
                sendBtn.innerHTML = '<i class="bi bi-send me-1"></i>Send';
            }
        } else if (sessions.length) {
            await activateSession(sessions[0].id);
        } else {
            await createAndActivateSession('New Chat 1');
        }
    })();
}
```

- [ ] **Step 4: Deploy and verify manually**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

Open the AI Chat page. Verify:
- Threads sidebar shows on desktop
- "New Chat" button creates a new thread
- Sending a message in a thread persists on reload
- Switching between threads loads their history

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_core.js web_client/js/pfm_features.js
git commit -m "feat: add chat threads sidebar with persistent session history

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 4: Frontend — Context handoff from Research + Portfolio Health

**Files:**
- Modify: `web_client/js/pfm_core.js`
- Modify: `web_client/index.html`
- Modify: `web_client/js/pfm_features.js`

**Interfaces:**
- Consumes: `window.openChatWithContext(threadName, openingMessage)` (defined in this task)
- Consumes: `R` object in `setupResearchPage` — `R.symbol`, `R.price`, `R.currency`, `R.fundamentals`, `R.llm`, `R.pos`
- Consumes: `data` object in `setupPortfolioHealth.renderResult` — `data.scores`, `data.recommendations`, `data.summary`

- [ ] **Step 1: Add `openChatWithContext` to `pfm_core.js`**

After the `apiClient` object definition (before the first usage of `window.apiClient`), add:

```javascript
function openChatWithContext(threadName, openingMessage) {
    window._chatPendingContext = { threadName, openingMessage };
    window.navigationManager.showPage('chat');
}
```

- [ ] **Step 2: Add "Chat about this" button to research workbench in `index.html`**

Find the research workbench toolbar (around line 1393):
```html
                                <button class="btn btn-outline-secondary" id="researchReportBtn" style="display:none;" ...>
```

Add after that button:
```html
                                <button class="btn btn-outline-primary" id="researchChatBtn" style="display:none;" title="Open AI chat pre-loaded with this research">
                                    <i class="bi bi-robot me-1"></i>Chat about this
                                </button>
```

- [ ] **Step 3: Add "Discuss with AI" button to portfolio health result bar in `index.html`**

Find the portfolio health summary bar (around line 1359):
```html
                                            <button class="btn btn-sm btn-outline-secondary" id="phRefreshBtn">
```

Add after the Refresh button, still inside the `.ms-auto.d-flex.gap-2`:
```html
                                            <button class="btn btn-sm btn-outline-primary" id="phChatBtn"><i class="bi bi-robot me-1"></i>Discuss with AI</button>
```

- [ ] **Step 4: Wire Research "Chat about this" button in `pfm_features.js`**

In the `load(sym)` function inside `setupResearchPage()`, find the line:
```javascript
            $('researchHint').textContent = '';
```

Add after it:
```javascript
            // Show "Chat about this" button and wire click handler
            const chatBtn = $('researchChatBtn');
            if (chatBtn) {
                chatBtn.style.display = '';
                chatBtn.onclick = () => {
                    const f = R.fundamentals || {};
                    const fmtV = (v, pct) => v == null ? '—' : (pct ? (v * 100).toFixed(1) + '%' : Fmt.num(v, 0, 2));
                    const recKey = f.recommendationKey ? (f.recommendationKey.replace(/_/g, ' ')) : null;
                    const upside = (R.price && R.llm && R.llm.fair_value)
                        ? ((R.llm.fair_value - R.price) / R.price * 100).toFixed(1) + '%'
                        : null;
                    let msg = `I'm researching ${R.symbol}${d.name ? ' (' + d.name + ')' : ''}.\n`;
                    msg += `Current price: ${money(R.price, R.currency)}.\n`;
                    if (R.llm) {
                        if (R.llm.fair_value) msg += `Fair value estimate: ${money(R.llm.fair_value, R.currency)}${upside ? ' (upside: ' + upside + ')' : ''}.\n`;
                        if (R.llm.recommendation) msg += `LLM recommendation: ${R.llm.recommendation.toUpperCase()}.\n`;
                        if (R.llm.confidence != null) msg += `Confidence: ${typeof R.llm.confidence === 'number' && R.llm.confidence <= 1 ? Math.round(R.llm.confidence * 100) + '%' : R.llm.confidence}.\n`;
                        if (R.llm.risks && R.llm.risks.length) msg += `Key risks: ${R.llm.risks.slice(0, 3).join('; ')}.\n`;
                        if (R.llm.catalysts && R.llm.catalysts.length) msg += `Key catalysts: ${R.llm.catalysts.slice(0, 3).join('; ')}.\n`;
                        if (R.llm.summary) msg += `\nLLM analysis summary:\n${R.llm.summary}\n`;
                        if (R.llm.rationale) msg += `\nRationale:\n${R.llm.rationale}\n`;
                    }
                    if (Object.keys(f).length) {
                        const keyFunds = ['trailingPE', 'forwardPE', 'trailingEps', 'dividendYield', 'beta', 'profitMargins', 'debtToEquity', 'returnOnEquity'];
                        const fundLines = keyFunds.filter(k => f[k] != null).map(k => `${humanizeFundKey(k)}: ${fmtFundVal(k, f[k])}`);
                        if (fundLines.length) msg += `\nKey fundamentals:\n${fundLines.join('\n')}\n`;
                    }
                    msg += `\nWhat are the key things I should consider before investing?`;
                    openChatWithContext(`Research: ${R.symbol}`, msg);
                };
            }
```

Note: `d` is the lookup result from `const d = await window.apiClient.researchLookup(sym)` that is in scope at this point. `R.llm` is set when `renderResearchReport` runs — if it's null (no LLM analysis yet), the message will just skip those sections.

However, `R.llm` is only populated when the user clicks Generate. Looking at the code, `R.llm` starts as `null`. Let's store the llm report on `R` when generate completes. Find in `setupResearchModal`:

```javascript
const report = await window.apiClient.generateResearchReport(_researchSymbol);
```

The research page uses `R` from `setupResearchPage` closure but `_researchSymbol` / `setupResearchModal` is a separate function. Check if `R.llm` is already populated somewhere.

Looking at the code, `R` is scoped to `setupResearchPage`. The `renderResearchReport` function in the outer scope (not inside `setupResearchPage`) doesn't update `R`. So `R.llm` will always be null unless we add a line to update it.

In the `load(sym)` function, after `renderResearchReport(report)` is called from `openResearchModal`, we're not in the `setupResearchPage` scope. The `R.llm` in the context button is from `setupResearchPage`'s `R` object.

The simplest fix: in the `load(sym)` function (inside `setupResearchPage`), `R.llm` is set to `null` initially. We need to also update it when a cached report is loaded. The `getResearchReport` call happens in `openResearchModal` (outside scope), but `load` in `setupResearchPage` calls `researchLookup` which includes `latest_note`. We can get the LLM data from `d.latest_note` if it has the fields.

Actually, looking more carefully, the `R.llm` is better set from `d` if a previous LLM analysis was cached. Let's set it in `load(sym)` from the lookup data:

Add after `R = { symbol: sym, ...`:
```javascript
            if (d.llm_summary || d.recommendation) {
                R.llm = { summary: d.llm_summary, recommendation: d.recommendation, fair_value: d.fair_value, confidence: d.confidence, risks: d.risks, catalysts: d.catalysts, rationale: d.rationale };
            }
```

Wait, I need to check what `researchLookup` actually returns. From CLAUDE.md: "GET /api/v1/research/{symbol}/lookup — snapshot (price, position, fundamentals, news, targets, latest note); no LLM". And `latest_note` has `llm_summary, sources`. So the `llm_summary` is stored in `latest_note.llm_summary`.

Let me simplify: just pass whatever is available. The context message works even if R.llm is null (those sections are skipped with null checks).

- [ ] **Step 5: Wire Portfolio Health "Discuss with AI" button in `pfm_features.js`**

In the `renderResult(data)` function inside `setupPortfolioHealth()`, find the line:
```javascript
        showState('result');
```

Add before it:
```javascript
        // Wire context chat button
        const chatBtn = $('phChatBtn');
        if (chatBtn) {
            chatBtn.onclick = () => {
                const scores = data.scores || {};
                const SCORE_LABELS = {
                    diversification: 'Diversification', risk_adjusted_return: 'Risk / Return',
                    income: 'Income', fees: 'Fees', tax_efficiency: 'Tax Efficiency',
                };
                let msg = `I've just reviewed my portfolio health analysis.\n\n`;
                msg += `**Scores:**\n`;
                Object.entries(scores).forEach(([key, s]) => {
                    msg += `- ${SCORE_LABELS[key] || key}: ${s.score || 0}/10 — ${s.reason || ''}\n`;
                });
                if (data.recommendations && data.recommendations.length) {
                    msg += `\n**Recommendations:**\n`;
                    data.recommendations.forEach(r => {
                        msg += `- [${r.category || ''}] ${r.action || ''}: ${r.rationale || ''}\n`;
                    });
                }
                if (data.summary) {
                    msg += `\n**Summary:** ${data.summary}\n`;
                }
                msg += `\nLet's discuss these findings. Which area should I focus on improving first?`;
                openChatWithContext('Portfolio Health Analysis', msg);
            };
        }
```

- [ ] **Step 6: Deploy and verify manually**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

Test:
1. Load a ticker in the Research workbench → "Chat about this" button appears → click it → lands on Chat page with a new thread named "Research: [TICKER]" with context message already sent and LLM response loading.
2. Run Portfolio Health analysis → "Discuss with AI" button appears in result bar → click it → lands on chat with all scores in the opening message.
3. Confirm both new threads appear in the sessions sidebar.

- [ ] **Step 7: Commit**

```bash
git add web_client/index.html web_client/js/pfm_core.js web_client/js/pfm_features.js
git commit -m "feat: add context handoff to AI chat from Research and Portfolio Health

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 5: Docs + final tests

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run full test suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e
```

Expected: All passing.

- [ ] **Step 2: Update `PROJECT_STATUS.md`**

Bump "Last updated" to 2026-06-22. Add to the Recent summary line:
- AI Chat: persistent named threads (DB-backed sessions, sidebar), context handoff from Research and Portfolio Health

- [ ] **Step 3: Update `CLAUDE.md`**

Add a new sub-section under `### LLM API`:

```markdown
#### Chat sessions
- `GET /api/v1/llm/chat/sessions` — list sessions, ordered by `last_message_at` DESC
- `POST /api/v1/llm/chat/sessions` body `{name}` → `{id, name}`
- `DELETE /api/v1/llm/chat/sessions/{id}` — 204 or 404
- `GET /api/v1/llm/chat/sessions/{id}/messages` → `{messages: [{role, content}]}`
- `POST /api/v1/llm/chat` — unchanged; auto-creates a `"New Chat"` session when `session_id` is absent/unknown
- `_get_history` / `_append_history` in `llm.py` now read/write `chat_sessions.messages` column directly (kv_cache no longer used for chat)
- v24 migration adds `chat_sessions` table (id TEXT PK, name, created_at, last_message_at, message_count, messages JSON)
```

Update the schema version note: `**Current schema version: 24.**`

Also note in the web client section: Chat page has a two-column layout (sessions sidebar + message area). `openChatWithContext(threadName, openingMessage)` in `pfm_core.js` sets `window._chatPendingContext` and navigates to the chat page; used by Research and Portfolio Health.

- [ ] **Step 4: Commit**

```bash
git add PROJECT_STATUS.md CLAUDE.md
git commit -m "docs: update PROJECT_STATUS and CLAUDE.md for chat threads feature

Co-Authored-By: Oz <oz-agent@warp.dev>"
```
