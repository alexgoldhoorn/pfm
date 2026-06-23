# Chat Timestamps & Thread Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-message timestamps to AI chat bubbles, show `last_message_at` on each thread in the sidebar, and allow inline thread renaming.

**Architecture:** Backend gains a `rename_chat_session` DB method + PATCH endpoint, and `_append_history` stores a `ts` ISO string on each message dict. Frontend updates `appendMessage` to render `Fmt.date(ts)` below bubbles, updates `renderSessionsList` to show the last-active time and a pencil rename icon, and adds `renameChatSession` to the API client.

**Tech Stack:** Python 3.13 / FastAPI / SQLite, Vanilla JS + Bootstrap 5.3, pytest + httpx for API tests, Node built-in test runner for JS smoke tests.

## Global Constraints

- Python target: 3.13. Run tooling with `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run …`
- Black line length 88. Pre-commit runs black + flake8 + autoflake on every commit.
- Commit messages: conventional commits (`feat:`, `fix:`, `test:`). Co-author line: `Co-Authored-By: Oz <oz-agent@warp.dev>`
- Run tests with: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v`
- Run JS tests with: `make test-js` (or `node --test web_client/js/tests/`)
- After any `web_client/` change: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`
- After any `portf_server/` or `portf_manager/` change: `docker exec portf_backend_dev kill -HUP 1`
- All timestamps use `Fmt.date(isoString)` — respects `PREFS.dateFormat` (iso/dmy/mdy), appends HH:MM automatically for datetime strings. No custom formatter.
- `chat_sessions` table was cleared before this feature — no migration or backward-compat needed.

---

### Task 1: DB method `rename_chat_session`

**Files:**
- Modify: `portf_manager/database.py` (after `delete_chat_session`, around line 3260)
- Test: `tests/test_database.py` (after the existing `test_get_nonexistent_session_returns_none` test, around line 1189)

**Interfaces:**
- Produces: `Database.rename_chat_session(id: str, name: str) -> bool` — returns `True` if a row was updated, `False` if the session was not found

- [ ] **Step 1: Write the failing tests**

Append to the `TestChatSessions` class in `tests/test_database.py`:

```python
    def test_rename_session(self, tmp_path):
        from portf_manager.database import Database

        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("s1", "Original")
        result = db.rename_chat_session("s1", "Renamed")
        assert result is True
        s = db.get_chat_session("s1")
        assert s["name"] == "Renamed"

    def test_rename_nonexistent_session_returns_false(self, tmp_path):
        from portf_manager.database import Database

        db = Database(str(tmp_path / "t.db"))
        result = db.rename_chat_session("missing", "Whatever")
        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_database.py::TestChatSessions::test_rename_session tests/test_database.py::TestChatSessions::test_rename_nonexistent_session_returns_false -v
```

Expected: FAIL with `AttributeError: 'Database' object has no attribute 'rename_chat_session'`

- [ ] **Step 3: Implement `rename_chat_session` in `database.py`**

Add directly after `delete_chat_session` (around line 3265):

```python
    def rename_chat_session(self, id: str, name: str) -> bool:
        """Rename a chat session. Returns True if a row was updated."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE chat_sessions SET name = ? WHERE id = ?", (name, id)
            )
            conn.commit()
        return cursor.rowcount > 0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_database.py::TestChatSessions::test_rename_session tests/test_database.py::TestChatSessions::test_rename_nonexistent_session_returns_false -v
```

Expected: 2 passed

- [ ] **Step 5: Run the full test suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
```

Expected: all passing (635+ tests)

- [ ] **Step 6: Commit**

```bash
git add portf_manager/database.py tests/test_database.py
git commit -m "feat: add rename_chat_session DB method

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 2: Backend PATCH endpoint + `ts` in `_append_history`

**Files:**
- Modify: `portf_server/routers/llm.py`
- Test: `tests/unit/test_chat_sessions.py`

**Interfaces:**
- Consumes: `Database.rename_chat_session(id: str, name: str) -> bool` (Task 1)
- Produces:
  - `PATCH /api/v1/llm/chat/sessions/{session_id}` body `{"name": "..."}` → `{"id": "...", "name": "..."}` or 404
  - Messages stored by `_append_history` now have shape `{"role": str, "content": str, "ts": str}` where `ts` is an ISO 8601 UTC datetime string

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_chat_sessions.py` inside `TestChatSessionEndpoints`:

```python
    async def test_rename_session(self, async_test_client: AsyncClient, auth_headers):
        create_resp = await async_test_client.post(
            "/api/v1/llm/chat/sessions",
            json={"name": "Original"},
            headers=auth_headers,
        )
        session_id = create_resp.json()["id"]
        rename_resp = await async_test_client.patch(
            f"/api/v1/llm/chat/sessions/{session_id}",
            json={"name": "Renamed"},
            headers=auth_headers,
        )
        assert rename_resp.status_code == 200
        assert rename_resp.json()["name"] == "Renamed"

        list_resp = await async_test_client.get(
            "/api/v1/llm/chat/sessions", headers=auth_headers
        )
        assert any(s["name"] == "Renamed" for s in list_resp.json())

    async def test_rename_nonexistent_session_returns_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        response = await async_test_client.patch(
            "/api/v1/llm/chat/sessions/nonexistent-id",
            json={"name": "Whatever"},
            headers=auth_headers,
        )
        assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_chat_sessions.py::TestChatSessionEndpoints::test_rename_session tests/unit/test_chat_sessions.py::TestChatSessionEndpoints::test_rename_nonexistent_session_returns_404 -v
```

Expected: FAIL with 405 Method Not Allowed (route not yet registered)

- [ ] **Step 3: Add `ts` to `_append_history` and add the PATCH endpoint**

At the top of `portf_server/routers/llm.py`, `datetime` is already imported (check; if not, add `from datetime import datetime`). Then:

**3a.** Update `_append_history` — change the `history.append(...)` line (around line 67) from:

```python
        history.append({"role": role, "content": content})
```

to:

```python
        history.append({"role": role, "content": content, "ts": datetime.utcnow().isoformat()})
```

**3b.** Add `RenameSessionRequest` Pydantic model near the other request models (around line 116, after `CreateSessionRequest`):

```python
class RenameSessionRequest(BaseModel):
    name: str
```

**3c.** Add the PATCH endpoint after the DELETE endpoint (around line 684, before the GET messages endpoint):

```python
@router.patch("/chat/sessions/{session_id}")
def rename_chat_session(
    session_id: str,
    request: RenameSessionRequest,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_llm),
):
    """Rename a chat session."""
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")
    updated = db.rename_chat_session(session_id, name)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"id": session_id, "name": name}
```

**Check `datetime` import:** look at the top of `llm.py` for existing imports. If `datetime` is not already imported, add:
```python
from datetime import datetime
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_chat_sessions.py -v
```

Expected: all 7 tests pass (5 existing + 2 new)

- [ ] **Step 5: Run the full test suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
```

Expected: all passing

- [ ] **Step 6: Reload the backend**

```bash
docker exec portf_backend_dev kill -HUP 1
```

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/llm.py tests/unit/test_chat_sessions.py
git commit -m "feat: add PATCH /chat/sessions/{id} rename endpoint and ts to messages

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 3: Frontend — `renameChatSession` API client method

**Files:**
- Modify: `web_client/js/pfm_core.js` (after `deleteChatSession`, around line 1287)

**Interfaces:**
- Consumes: `PATCH /api/v1/llm/chat/sessions/{id}` (Task 2)
- Produces: `window.apiClient.renameChatSession(id: string, name: string) -> Promise<{id, name}>`

- [ ] **Step 1: Add `renameChatSession` to the apiClient object**

In `pfm_core.js`, after the `deleteChatSession` method (around line 1287), add:

```js
        async renameChatSession(id, name) {
            const response = await fetch(this.baseURL + '/api/v1/llm/chat/sessions/' + id, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ name })
            });
            if (!response.ok) throw new Error('Failed to rename session');
            return response.json();
        },
```

- [ ] **Step 2: Run JS smoke tests to verify no breakage**

```bash
make test-js
```

Expected: all passing (the smoke test catches broken JS scope)

- [ ] **Step 3: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat: add renameChatSession API client method

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 4: Frontend — message timestamps + sidebar timestamp + thread rename

**Files:**
- Modify: `web_client/js/pfm_features.js`

**Interfaces:**
- Consumes:
  - `window.apiClient.renameChatSession(id, name)` (Task 3)
  - `Fmt.date(isoString)` — already defined in `pfm_core.js`, returns formatted date+time string respecting `PREFS.dateFormat`
  - `esc(s)` — already defined in `pfm_core.js`
  - Messages from `getChatSessionMessages` now have shape `{role, content, ts}` (Task 2)

**Changes overview:**
1. `appendMessage(role, text, ts = null)` — add ts param, render timestamp below bubble
2. `activateSession` — pass `m.ts` when calling `appendMessage` for history messages
3. `doSend` — capture `ts` before sending user message; pass ts to both `appendMessage` calls; update `last_message_at` in local sessions
4. `doExtract` and pending-context `appendMessage` calls — pass `new Date().toISOString()` for live messages
5. `createAndActivateSession` — add `last_message_at` to the new session object
6. `renderSessionsList` — add `Fmt.date(s.last_message_at)` below thread name; add pencil icon; add rename logic in click handler

- [ ] **Step 1: Update `appendMessage` to accept and render `ts`**

Current `appendMessage` (around line 503):
```js
    function appendMessage(role, text) {
        removeEmpty();
        const isUser = role === 'user';
        ...
        div.className = `d-flex ${isUser ? 'justify-content-end' : 'justify-content-start'}`;
        div.innerHTML = `
            <div class="px-3 py-2 rounded-3 chat-bubble ${isUser ? 'bg-primary text-white' : 'chat-bubble-assistant border'}"
                 style="max-width:80%;word-break:break-word;${extraStyle}">${body}</div>`;
        messagesEl.appendChild(div);
        scrollBottom();
    }
```

Replace with:
```js
    function appendMessage(role, text, ts = null) {
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
        const tsHtml = ts
            ? `<div class="text-muted ${isUser ? 'text-end' : ''}" style="font-size:0.7rem;">${Fmt.date(ts)}</div>`
            : '';
        const div = document.createElement('div');
        div.className = `d-flex ${isUser ? 'justify-content-end' : 'justify-content-start'}`;
        div.innerHTML = `
            <div>
                <div class="px-3 py-2 rounded-3 chat-bubble ${isUser ? 'bg-primary text-white' : 'chat-bubble-assistant border'}"
                     style="max-width:80%;word-break:break-word;${extraStyle}">${body}</div>
                ${tsHtml}
            </div>`;
        messagesEl.appendChild(div);
        scrollBottom();
    }
```

- [ ] **Step 2: Update `activateSession` to pass `m.ts`**

In `activateSession` (around line 653), change:
```js
                messages.forEach(m => appendMessage(m.role, m.content));
```
to:
```js
                messages.forEach(m => appendMessage(m.role, m.content, m.ts || null));
```

- [ ] **Step 3: Update `doSend` to capture timestamps and update `last_message_at`**

Current `doSend` (around line 664):
```js
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
```

Replace with:
```js
    async function doSend() {
        const text = inputEl.value.trim();
        if (!text) return;
        inputEl.value = '';
        const userTs = new Date().toISOString();
        appendMessage('user', text, userTs);
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
            const data = await window.apiClient.sendChat(text, sessionId);
            appendMessage('assistant', data.answer || '(no response)', new Date().toISOString());
            const s = sessions.find(x => x.id === sessionId);
            if (s) {
                s.message_count = (s.message_count || 0) + 2;
                s.last_message_at = new Date().toISOString();
                renderSessionsList();
            }
        } catch (err) {
            appendMessage('assistant', 'Error: ' + err.message);
        } finally {
            sendBtn.disabled = false;
            sendBtn.innerHTML = '<i class="bi bi-send me-1"></i>Send';
        }
    }
```

- [ ] **Step 4: Add `ts` to `doExtract` and pending-context `appendMessage` calls**

In `doExtract` (around line 689):
```js
        appendMessage('user', '[Broker statement — extracting transactions…]');
```
→
```js
        appendMessage('user', '[Broker statement — extracting transactions…]', new Date().toISOString());
```

Find all remaining `appendMessage('assistant', ...)` and `appendMessage('user', ...)` calls inside `setupChatPage` that don't yet have a ts argument (the pending-context blocks around lines 732, 737, 741, 758, 763, 767). Add `new Date().toISOString()` as the third argument to each. Example pattern to find and update:

```js
// Before:
appendMessage('user', pending.openingMessage);
// After:
appendMessage('user', pending.openingMessage, new Date().toISOString());

// Before:
appendMessage('assistant', data.answer || '(no response)');
// After:
appendMessage('assistant', data.answer || '(no response)', new Date().toISOString());

// Before:
appendMessage('assistant', 'Error: ' + err.message);
// After:
appendMessage('assistant', 'Error: ' + err.message, new Date().toISOString());
```

- [ ] **Step 5: Update `createAndActivateSession` to store `last_message_at`**

Around line 658:
```js
    async function createAndActivateSession(name) {
        const s = await window.apiClient.createChatSession(name);
        sessions.unshift({ id: s.id, name: s.name, message_count: 0 });
        await activateSession(s.id);
    }
```

Replace with:
```js
    async function createAndActivateSession(name) {
        const s = await window.apiClient.createChatSession(name);
        sessions.unshift({ id: s.id, name: s.name, message_count: 0, last_message_at: new Date().toISOString() });
        await activateSession(s.id);
    }
```

- [ ] **Step 6: Update `renderSessionsList` — sidebar timestamp + pencil icon**

Current session button template (around line 611):
```js
        sessionsList.innerHTML = sessions.map(s => `
            <button class="list-group-item list-group-item-action d-flex align-items-center gap-2 py-2 px-3 ${s.id === sessionId ? 'active' : ''}"
                    data-session-id="${esc(s.id)}" style="font-size:0.85rem;">
                <span class="text-truncate flex-grow-1">${esc(s.name)}</span>
                <span class="badge bg-secondary rounded-pill ms-auto" style="font-size:0.7rem;">${s.message_count || 0}</span>
                <i class="bi bi-x chat-delete-session flex-shrink-0" title="Delete thread" style="cursor:pointer;opacity:0.6;"></i>
            </button>`).join('');
```

Replace with:
```js
        sessionsList.innerHTML = sessions.map(s => `
            <button class="list-group-item list-group-item-action d-flex align-items-center gap-2 py-2 px-3 ${s.id === sessionId ? 'active' : ''}"
                    data-session-id="${esc(s.id)}" style="font-size:0.85rem;">
                <span class="flex-grow-1 text-truncate" style="min-width:0;">
                    <span class="d-block text-truncate">${esc(s.name)}</span>
                    <span class="text-muted d-block" style="font-size:0.7rem;">${Fmt.date(s.last_message_at) || ''}</span>
                </span>
                <span class="badge bg-secondary rounded-pill" style="font-size:0.7rem;">${s.message_count || 0}</span>
                <i class="bi bi-pencil chat-rename-session flex-shrink-0" title="Rename thread" style="cursor:pointer;opacity:0.6;font-size:0.8rem;"></i>
                <i class="bi bi-x chat-delete-session flex-shrink-0" title="Delete thread" style="cursor:pointer;opacity:0.6;"></i>
            </button>`).join('');
```

- [ ] **Step 7: Add rename logic to the click handler in `renderSessionsList`**

The existing click handler inside `sessionsList.querySelectorAll('[data-session-id]').forEach(btn => {...})` (around line 619) handles delete and session activation. Add a rename branch at the top of the click handler, before the delete check:

```js
        sessionsList.querySelectorAll('[data-session-id]').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                // Rename
                if (e.target.classList.contains('chat-rename-session')) {
                    e.stopPropagation();
                    const id = btn.dataset.sessionId;
                    const currentName = sessions.find(x => x.id === id)?.name || '';
                    const nameWrap = btn.querySelector('.flex-grow-1');
                    const input = document.createElement('input');
                    input.type = 'text';
                    input.className = 'form-control form-control-sm';
                    input.value = currentName;
                    input.style.fontSize = '0.85rem';
                    nameWrap.replaceWith(input);
                    input.focus();
                    input.select();

                    let committed = false;
                    const commit = async () => {
                        if (committed) return;
                        committed = true;
                        const newName = input.value.trim();
                        if (newName && newName !== currentName) {
                            try {
                                await window.apiClient.renameChatSession(id, newName);
                                const s = sessions.find(x => x.id === id);
                                if (s) s.name = newName;
                                if (sessionId === id && threadNameEl) threadNameEl.textContent = newName;
                            } catch (_) { /* revert silently */ }
                        }
                        renderSessionsList();
                    };
                    input.addEventListener('blur', commit);
                    input.addEventListener('keydown', ke => {
                        if (ke.key === 'Enter') { ke.preventDefault(); input.blur(); }
                        if (ke.key === 'Escape') {
                            committed = true;
                            renderSessionsList();
                        }
                    });
                    return;
                }
                // Delete
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
```

- [ ] **Step 8: Run JS smoke tests**

```bash
make test-js
```

Expected: all passing

- [ ] **Step 9: Build and deploy the web client**

```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```

- [ ] **Step 10: Manual verification**

1. Open the Chat page in the browser.
2. Send a message — confirm timestamp appears below both the user bubble and the assistant bubble.
3. The thread in the sidebar should show the time of last activity.
4. Click the pencil icon on a thread — confirm inline input appears, pre-filled with the thread name.
5. Type a new name and press Enter — confirm it updates in the sidebar and in the active thread header, and persists after page reload.
6. Press Escape during rename — confirm it cancels without changes.
7. Switch between `PREFS.dateFormat` options in Settings and verify timestamps reformat accordingly.

- [ ] **Step 11: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: add message timestamps and thread rename to chat UI

Co-Authored-By: Oz <oz-agent@warp.dev>"
```
