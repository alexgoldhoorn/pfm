# Spending Rule Editing + AI Pattern Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user fix an AI-suggested category rule's pattern *before* it's created (editable pattern field in the AI-suggest review panel), fix an existing rule's pattern/category *after* the fact (edit-in-place on the Rules list, which today only supports add/delete), and reduce how often either is needed by teaching the suggestion prompt to strip real-world description noise (leading card/transaction-reference numbers, trailing location+date+reference codes) before extracting a merchant-name pattern.

**Architecture:** One new backend endpoint (`PUT /api/v1/spending/rules/{id}`) plus two new `Database` methods, mirroring this router's existing `PUT /api/v1/spending/{id}` category-edit pattern exactly. Two independent frontend additions to `pfm_features.js` (rule edit-in-place; an editable pattern input in the existing AI-suggest review panel). One pure prompt-text change with a regression test.

**Tech Stack:** Python 3.13 / FastAPI (backend), vanilla JS / Bootstrap 5 (frontend, no build step), pytest.

## Global Constraints

- Code style: black (line length 88); comments on the line before the code they describe; type hints on all function signatures; Google-style docstrings.
- Never commit real personal/financial data — test fixtures use fictional merchant names.
- `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e` must pass after every backend task.
- `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501` must report 0 warnings.
- `node --test web_client/js/tests/` (or `make test-js`) must pass after every frontend task.
- The matching engine itself (`_apply_rules()`, substring match) does not change in this plan — only what pattern text ends up stored, and whether it can be edited before/after creation.
- Both `PROJECT_STATUS.md` and `CLAUDE.md` must be updated (mandatory project convention).
- Web client changes require rebuild + redeploy: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`. Backend changes: `docker exec portf_backend_dev kill -HUP 1`.

---

## Task 1: Backend `PUT /api/v1/spending/rules/{id}`

**Files:**
- Modify: `portf_manager/database.py` (two new methods, placed after `list_spending_rules` and before `delete_spending_rule`, which currently ends at line 2898 with `return cursor.rowcount > 0`)
- Modify: `portf_server/routers/spending.py` (new `SpendingRuleUpdateBody` model after the existing `SpendingRuleResponse` model; new endpoint placed after `create_rule` and before `delete_rule`)
- Test: `tests/unit/test_spending_api.py` (new tests immediately after `test_rules_crud`, which currently ends with `assert client.get("/api/v1/spending/rules", headers=HEADERS).json() == []`)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `Database.get_spending_rule(rule_id: int) -> Optional[Dict]`, `Database.update_spending_rule(rule_id: int, **kwargs) -> bool` (valid kwargs: `pattern`, `category`). `PUT /api/v1/spending/rules/{rule_id}` with body `{"pattern": <str, optional>, "category": <str, optional>}` → `SpendingRuleResponse` on success, 400 if neither field is set or a provided field is empty after trimming, 404 if the rule doesn't exist. Consumed by Task 2's frontend.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_spending_api.py`, add immediately after `test_rules_crud`:

```python
def test_update_rule_pattern_and_category(tmp_path):
    client, _ = _make_client(tmp_path)
    rule_id = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    ).json()["id"]

    r = client.put(
        f"/api/v1/spending/rules/{rule_id}",
        json={"pattern": "MERCAT", "category": "Food"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json() == {"id": rule_id, "pattern": "MERCAT", "category": "Food"}

    listed = client.get("/api/v1/spending/rules", headers=HEADERS).json()
    assert listed == [{"id": rule_id, "pattern": "MERCAT", "category": "Food"}]


def test_update_rule_pattern_only(tmp_path):
    client, _ = _make_client(tmp_path)
    rule_id = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    ).json()["id"]

    r = client.put(
        f"/api/v1/spending/rules/{rule_id}",
        json={"pattern": "MERCAT"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json() == {"id": rule_id, "pattern": "MERCAT", "category": "Groceries"}


def test_update_rule_empty_body_rejected(tmp_path):
    client, _ = _make_client(tmp_path)
    rule_id = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    ).json()["id"]

    r = client.put(f"/api/v1/spending/rules/{rule_id}", json={}, headers=HEADERS)
    assert r.status_code == 400


def test_update_rule_blank_pattern_rejected(tmp_path):
    client, _ = _make_client(tmp_path)
    rule_id = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    ).json()["id"]

    r = client.put(
        f"/api/v1/spending/rules/{rule_id}",
        json={"pattern": "   "},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_update_missing_rule(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.put(
        "/api/v1/spending/rules/999999",
        json={"pattern": "X"},
        headers=HEADERS,
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k update_rule -v`
Expected: all FAIL with 405 Method Not Allowed (no PUT route on `/rules/{rule_id}` yet).

- [ ] **Step 3: Add the two Database methods**

In `portf_manager/database.py`, immediately after `list_spending_rules` (it ends with `return [dict(row) for row in cursor.fetchall()]`) and before `delete_spending_rule`, add:

```python
    def get_spending_rule(self, rule_id: int) -> Optional[Dict]:
        """Get a spending category rule by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM spending_rules WHERE id = ?", (rule_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_spending_rule(self, rule_id: int, **kwargs) -> bool:
        """Update a spending rule's pattern and/or category."""
        valid_fields = {"pattern", "category"}
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}
        if not update_fields:
            return False
        with self.get_connection() as conn:
            set_clause = ", ".join(f"{field} = ?" for field in update_fields)
            values = list(update_fields.values()) + [rule_id]
            cursor = conn.execute(
                f"UPDATE spending_rules SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
            return cursor.rowcount > 0
```

- [ ] **Step 4: Add the endpoint**

In `portf_server/routers/spending.py`, immediately after the `SpendingRuleResponse` model (it ends with the closing line before `class SpendingSummaryResponse`), add:

```python
class SpendingRuleUpdateBody(BaseModel):
    pattern: Optional[str] = None
    category: Optional[str] = None
```

Then, immediately after the `create_rule` function (it ends with `return SpendingRuleResponse(id=rule_id, pattern=body.pattern, category=body.category)`) and before `delete_rule`, add:

```python
@router.put("/rules/{rule_id}", response_model=SpendingRuleResponse)
async def update_rule(
    rule_id: int,
    body: SpendingRuleUpdateBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Edit an existing spending category rule's pattern and/or category."""
    update_kwargs = {}
    if body.pattern is not None:
        pattern = body.pattern.strip()
        if not pattern:
            raise HTTPException(status_code=400, detail="Pattern cannot be empty")
        update_kwargs["pattern"] = pattern
    if body.category is not None:
        category = body.category.strip()
        if not category:
            raise HTTPException(status_code=400, detail="Category cannot be empty")
        update_kwargs["category"] = category
    if not update_kwargs:
        raise HTTPException(
            status_code=400, detail="Provide at least one of pattern or category"
        )
    if not db.update_spending_rule(rule_id, **update_kwargs):
        raise HTTPException(status_code=404, detail="Rule not found")
    updated = db.get_spending_rule(rule_id)
    return SpendingRuleResponse(**updated)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k update_rule -v`
Expected: 5 passed.

- [ ] **Step 6: Run the full spending test file and lint**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -v`
Expected: all pass, no regressions.

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: 0 warnings (run `uv run black portf_manager/database.py portf_server/routers/spending.py` first if needed).

- [ ] **Step 7: Run the full unit suite**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all pass, no regressions.

- [ ] **Step 8: Commit**

```bash
git add portf_manager/database.py portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: add PUT /api/v1/spending/rules/{id}

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: Frontend rule edit-in-place

**Files:**
- Modify: `web_client/js/pfm_core.js` (new `updateSpendingRule(id, payload)` method, immediately after the existing `deleteSpendingRule` method)
- Modify: `web_client/js/pfm_features.js` (`_renderSpendingRules()` gains an edit affordance; new `window.editSpendingRule` function)

**Interfaces:**
- Consumes: `PUT /api/v1/spending/rules/{id}` from Task 1.
- Produces: `window.apiClient.updateSpendingRule(id: number, payload: {pattern?: string, category?: string}) -> Promise<object>`, `window.editSpendingRule(id: number) -> void`. Not consumed by any other task.

This task adds no automated tests, matching this codebase's existing precedent — DOM click-handler/edit-in-place wiring isn't unit-tested anywhere in this file (e.g. `editManualAssetAmount` on the Net Worth page, which this mirrors, has none either). Verify manually per Step 4.

- [ ] **Step 1: Add the API client method**

In `web_client/js/pfm_core.js`, immediately after the existing `deleteSpendingRule` method (it ends with `return response.json();` followed by `},`), add:

```javascript
        async updateSpendingRule(id, payload) {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules/' + id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!response.ok) {
                let detail = 'Failed to update rule';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
```

- [ ] **Step 2: Add the edit-in-place row rendering and handler**

In `web_client/js/pfm_features.js`, find `_renderSpendingRules`:

```javascript
function _renderSpendingRules(rules) {
    const body = document.getElementById('spRulesBody');
    if (!body) return;
    body.innerHTML = rules.length ? rules.map(r => `
        <tr>
            <td class="ps-3">${esc(r.pattern)}</td>
            <td>${esc(r.category)}</td>
            <td class="pe-3 text-end"><button class="btn btn-sm btn-outline-danger" onclick="window.deleteSpendingRule(${r.id})"><i class="bi bi-trash"></i></button></td>
        </tr>`).join('') : '<tr><td colspan="3" class="text-center text-muted py-2">No rules yet.</td></tr>';
}
```

Replace with:

```javascript
function _renderSpendingRules(rules) {
    const body = document.getElementById('spRulesBody');
    if (!body) return;
    body.innerHTML = rules.length ? rules.map(r => `
        <tr>
            <td class="ps-3" id="spRulePatternCell${r.id}" data-value="${escapeForAttr(r.pattern)}">${esc(r.pattern)}</td>
            <td id="spRuleCategoryCell${r.id}" data-value="${escapeForAttr(r.category)}">${esc(r.category)}</td>
            <td class="pe-3 text-end">
                <button class="btn btn-sm btn-outline-secondary" onclick="window.editSpendingRule(${r.id})" title="Edit"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-outline-danger" onclick="window.deleteSpendingRule(${r.id})" title="Delete"><i class="bi bi-trash"></i></button>
            </td>
        </tr>`).join('') : '<tr><td colspan="3" class="text-center text-muted py-2">No rules yet.</td></tr>';
}
```

Then, immediately after `window.deleteSpendingRule`'s closing `};`, add:

```javascript
window.editSpendingRule = function (id) {
    const patternCell = document.getElementById(`spRulePatternCell${id}`);
    const categoryCell = document.getElementById(`spRuleCategoryCell${id}`);
    if (!patternCell || !categoryCell || patternCell.dataset.editing) return;
    patternCell.dataset.editing = '1';
    const originalPattern = patternCell.dataset.value;
    const originalCategory = categoryCell.dataset.value;
    patternCell.innerHTML = `<input class="form-control form-control-sm" id="spRulePatternInput${id}" value="${escapeForAttr(originalPattern)}">`;
    categoryCell.innerHTML = `<input class="form-control form-control-sm" id="spRuleCategoryInput${id}" value="${escapeForAttr(originalCategory)}">`;
    const patternInput = document.getElementById(`spRulePatternInput${id}`);
    const categoryInput = document.getElementById(`spRuleCategoryInput${id}`);
    patternInput.focus();
    patternInput.select();

    let done = false;
    const finish = async (commit) => {
        if (done) return;
        done = true;
        const newPattern = patternInput.value.trim();
        const newCategory = categoryInput.value.trim();
        if (!commit) { await _refreshSpendingData(); return; }
        if (!newPattern || !newCategory) {
            alert('Pattern and category cannot be empty.');
            await _refreshSpendingData();
            return;
        }
        if (newPattern === originalPattern && newCategory === originalCategory) {
            await _refreshSpendingData();
            return;
        }
        try {
            await window.apiClient.updateSpendingRule(id, { pattern: newPattern, category: newCategory });
        } catch (err) {
            alert('Error: ' + err.message);
        }
        await _refreshSpendingData();
    };
    [patternInput, categoryInput].forEach(inp => {
        inp.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') finish(true);
            if (e.key === 'Escape') finish(false);
        });
        inp.addEventListener('blur', () => finish(true));
    });
};
```

(Both inputs commit together on either one's blur/Enter — editing a rule's pattern and category is a single logical edit, matching the spec's "click a pencil icon, both cells become inputs" description. `done` guards against `blur` firing on both inputs during the same commit.)

- [ ] **Step 3: Verify**

Run: `node --check web_client/js/pfm_core.js && node --check web_client/js/pfm_features.js`
Expected: prints nothing (syntax OK).

Run: `make test-js`
Expected: all 49 existing tests still pass (no new tests added, per this task's no-test-coverage precedent).

- [ ] **Step 4: Verify manually**

Rebuild and load the page:
```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
On the Spending page's Rules card: click the pencil icon on an existing rule, confirm both Pattern and Category become editable inputs, change one, press Enter, confirm the row updates and the change persists after reloading the page. Click pencil again, press Escape, confirm nothing changed. Try clearing the pattern to empty and pressing Enter — confirm an alert appears and the original value is restored (via the reload from `_refreshSpendingData()`).

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_core.js web_client/js/pfm_features.js
git commit -m "feat: add edit-in-place to the Spending Rules list

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 3: Editable pattern field in the AI-suggest review panel

**Files:**
- Modify: `web_client/js/pfm_features.js` (`_renderSpSuggestReviewPanel()`)

**Interfaces:**
- Consumes: `window._spSuggestGroups` (existing, unchanged shape except now also user-editable via this task's new input).
- Produces: nothing new consumed elsewhere — `_applySpSuggestions` (unchanged) already reads `g.suggestedPattern` from this same state when calling `createSpendingRule`.

No automated tests for this task either — same DOM-wiring precedent as Task 2 and the panel's existing category `<select>` (also untested at this layer).

- [ ] **Step 1: Add the pattern input and its listener**

In `web_client/js/pfm_features.js`, find `_renderSpSuggestReviewPanel`'s row template:

```javascript
                ${groups.map((g, i) => `
                    <div class="d-flex align-items-center gap-2 mb-1">
                        <input type="checkbox" class="form-check-input sp-suggest-check" data-idx="${i}" checked>
                        <span class="small flex-grow-1">${esc(g.description)} <span class="text-muted">(&times;${g.ids.length})</span></span>
                        <select class="form-select form-select-sm w-auto sp-suggest-category" data-idx="${i}">
                            ${categories.map(c => `<option value="${esc(c)}" ${c === g.suggestedCategory ? 'selected' : ''}>${esc(c)}</option>`).join('')}
                        </select>
                    </div>`).join('')}
```

Replace with:

```javascript
                ${groups.map((g, i) => `
                    <div class="d-flex align-items-center gap-2 mb-1">
                        <input type="checkbox" class="form-check-input sp-suggest-check" data-idx="${i}" checked>
                        <span class="small flex-grow-1">${esc(g.description)} <span class="text-muted">(&times;${g.ids.length})</span></span>
                        <input type="text" class="form-control form-control-sm sp-suggest-pattern" style="max-width:160px;" data-idx="${i}" value="${escapeForAttr(g.suggestedPattern)}" title="Rule pattern (matches as a substring)">
                        <select class="form-select form-select-sm w-auto sp-suggest-category" data-idx="${i}">
                            ${categories.map(c => `<option value="${esc(c)}" ${c === g.suggestedCategory ? 'selected' : ''}>${esc(c)}</option>`).join('')}
                        </select>
                    </div>`).join('')}
```

Then find the existing listener-wiring block right after `panel.innerHTML = ...;`:

```javascript
    panel.querySelectorAll('.sp-suggest-category').forEach(sel => {
        sel.addEventListener('change', () => {
            window._spSuggestGroups[parseInt(sel.dataset.idx, 10)].suggestedCategory = sel.value;
        });
    });
```

Add a sibling block immediately before it:

```javascript
    panel.querySelectorAll('.sp-suggest-pattern').forEach(inp => {
        inp.addEventListener('input', () => {
            window._spSuggestGroups[parseInt(inp.dataset.idx, 10)].suggestedPattern = inp.value;
        });
    });
    panel.querySelectorAll('.sp-suggest-category').forEach(sel => {
        sel.addEventListener('change', () => {
            window._spSuggestGroups[parseInt(sel.dataset.idx, 10)].suggestedCategory = sel.value;
        });
    });
```

- [ ] **Step 2: Verify**

Run: `node --check web_client/js/pfm_features.js`
Expected: prints nothing.

Run: `make test-js`
Expected: all 49 tests still pass.

- [ ] **Step 3: Verify manually**

Rebuild/redeploy as in Task 2 Step 4. On the Spending page: select some uncategorized rows, click "Suggest categories (AI)", confirm each review row now shows an editable pattern input pre-filled with the AI's suggestion, edit one to something shorter, click Apply, then check the Rules card and confirm the new rule's pattern matches your edited value, not the original AI suggestion.

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: make the AI-suggested rule pattern editable before Apply

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 4: Improve the AI-suggest prompt to strip description noise

**Files:**
- Modify: `portf_server/routers/spending.py` (`_build_suggest_prompt`)
- Test: `tests/unit/test_spending_api.py` (new test near the other prompt-adjacent tests, or at the end of the file)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing consumed by other tasks — this is a pure prompt-text change to an existing function, same signature (`_build_suggest_prompt(descriptions: List[str]) -> str`).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_spending_api.py` (end of file is fine):

```python
def test_suggest_prompt_instructs_stripping_transaction_noise():
    from portf_server.routers.spending import _build_suggest_prompt

    prompt = _build_suggest_prompt(["767002813178EXAMPLE MERCHANT\\CITY\\ES0000000019"])
    assert "card/transaction-reference" in prompt
    assert "location+date+reference" in prompt
    assert "767002813178EXAMPLE MERCHANT\\CITY\\ES0000000019" in prompt
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k stripping_transaction_noise -v`
Expected: FAIL — `assert "card/transaction-reference" in prompt` fails (current prompt text doesn't mention this).

- [ ] **Step 3: Update the prompt**

In `portf_server/routers/spending.py`, find `_build_suggest_prompt`:

```python
def _build_suggest_prompt(descriptions: List[str]) -> str:
    lines = "\n".join(f"- {d}" for d in descriptions)
    return f"""
You categorize bank statement transaction descriptions into everyday spending
categories. For each description below, suggest ONE category from this set
(or a similarly short new one if none fit): Groceries, Dining, Transport,
Utilities, Housing, Health, Entertainment, Shopping, Income, Subscriptions,
Other.

Also suggest a short "pattern" — a distinctive substring of the description
(e.g. the merchant name) that could be reused to auto-match future rows with
the same category. Keep it as short as possible while still being specific
to this merchant (avoid matching unrelated transactions).

Return ONLY a JSON array, one object per description, in the same order:
[{{"description": "...", "category": "...", "suggested_pattern": "..."}}]

Descriptions:
{lines}
"""
```

Replace with:

```python
def _build_suggest_prompt(descriptions: List[str]) -> str:
    lines = "\n".join(f"- {d}" for d in descriptions)
    return f"""
You categorize bank statement transaction descriptions into everyday spending
categories. For each description below, suggest ONE category from this set
(or a similarly short new one if none fit): Groceries, Dining, Transport,
Utilities, Housing, Health, Entertainment, Shopping, Income, Subscriptions,
Other.

Also suggest a short "pattern" — a distinctive substring of the description
(e.g. the merchant name) that could be reused to auto-match future rows with
the same category. Keep it as short as possible while still being specific
to this merchant (avoid matching unrelated transactions).

Real bank descriptions often carry noise around the merchant name: a leading
numeric card/transaction-reference number (e.g. "767002813178EXAMPLE
MERCHANT...") and/or a trailing location+date+reference code (e.g.
"...\\CITY\\ES0000000019"). Ignore that noise — the pattern must be just the
clean merchant name (e.g. "EXAMPLE MERCHANT"), never the numeric prefix or
the trailing location/date/reference suffix.

Return ONLY a JSON array, one object per description, in the same order:
[{{"description": "...", "category": "...", "suggested_pattern": "..."}}]

Descriptions:
{lines}
"""
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k stripping_transaction_noise -v`
Expected: 1 passed.

- [ ] **Step 5: Run the full spending test file, lint, and full unit suite**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -v`
Expected: all pass.

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: 0 warnings.

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "fix: teach the AI-suggest prompt to ignore transaction-reference noise

Real bank descriptions (e.g. Abanca card purchases) carry a leading
card/transaction-reference number and a trailing
\\CITY\\ESyymmddNNNN-style location/date/reference code around the
merchant name. Without guidance the suggested rule pattern could
include this noise, making it too specific to ever match again.

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 5: Documentation updates

**Files:**
- Modify: `CLAUDE.md` (Spending Tracking section)
- Modify: `PROJECT_STATUS.md` (new "Recent" line)

- [ ] **Step 1: Update CLAUDE.md**

Find the bullet added by the prior feature that starts with `- **AI category
suggestions on already-imported rows**:` in the Spending Tracking section
(it currently ends with `...A "Select all uncategorized" button...no
auto-chaining needed...surfaces a smaller remaining set).`). Append one more
sentence to the end of that bullet (before its final period), covering: the
review panel now also has an editable pattern field per suggestion (not just
category), so an AI-suggested pattern can be corrected before it becomes a
rule.

Then find the `GET/POST /api/v1/spending/rules, DELETE
/api/v1/spending/rules/{id}` reference (in the bullet listing
`GET /api/v1/spending/` filters) and change it to also mention
`PUT /api/v1/spending/rules/{id}` (edit an existing rule's pattern/category
— the Rules card on the Spending page now has an edit-in-place pencil icon
per row, not just add/delete).

- [ ] **Step 2: Update PROJECT_STATUS.md**

Bump "Last updated" to today's actual date (check with `date +%F`) and add a
new "Recent" line (next sequential version number after whatever is
currently the top entry) summarizing: the AI-suggest review panel's pattern
is now editable before Apply, the existing Rules list gained edit-in-place
(previously add/delete only), and the suggestion prompt now explicitly
strips leading card-reference-number and trailing
location+date+reference-code noise from real bank descriptions before
extracting a merchant-name pattern — motivated by production Abanca data
where every transaction's raw description was otherwise unique even for
repeat visits to the same merchant.

- [ ] **Step 3: Verify only docs changed**

Run: `git diff --stat CLAUDE.md PROJECT_STATUS.md`
Expected: both files show changes; `git status --short` shows no other file
modified.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document rule editing + AI-suggest pattern quality

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## After this plan ships

Task 1 and Task 4 touch `portf_server/`, so `docker exec portf_backend_dev
kill -HUP 1` is needed (the dev container also auto-reloads via watchfiles,
but HUP is the documented/reliable path). Tasks 2 and 3 touch `web_client/`,
so `docker compose build web && docker stop portf_web && WEB_PORT=8080
docker compose up -d web` is needed. Not part of the plan's tasks — call
this out separately once implementation is verified.
