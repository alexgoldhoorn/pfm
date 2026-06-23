# Task 2: API Endpoints — Report

## Status
**DONE**

## Commits
- `844eb19` — feat: add /export/yahoo-finance and /export/simply-wall-st endpoints

## Test Results
```
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
Result: 561 passed, 6 skipped (no new failures)
```

## Self-Review

### Implementation
Both endpoints added to `portf_server/routers/exports.py`:
- `GET /api/v1/export/yahoo-finance?portfolio_id=<int>&mode=<str>` ✓
- `GET /api/v1/export/simply-wall-st?portfolio_id=<int>&mode=<str>` ✓

### Headers
Both endpoints set required CORS-exposed headers:
- `X-Skipped-Count: N` — count of skipped assets
- `X-Skipped-Symbols: SYM1,SYM2` — comma-separated list
- `Access-Control-Expose-Headers: X-Skipped-Count,X-Skipped-Symbols` ✓

### CSV Format
- Yahoo Finance: UTF-8 BOM + CSV headers matching spec
- Simply Wall St: UTF-8 BOM + CSV headers matching spec
- Both use existing pattern: `b"\xef\xbb\xbf" + content.encode("utf-8")` ✓

### Integration
- Consumes `build_yahoo_finance_csv()` and `build_simply_wall_st_csv()` from Task 1 ✓
- Reuses existing auth (`_auth` dependency) ✓
- Reuses existing database dependency ✓
- Pre-commit hooks passed: black, flake8, autoflake ✓

### Concerns
None. All requirements met, all tests passing.
