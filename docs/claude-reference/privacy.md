# Privacy and demo data

> Moved out of `CLAUDE.md` to keep it short. CLAUDE.md points here.

Public repo — never commit real personal or financial data.

**Forbidden:** real API keys/tokens/passwords; real Spreadsheet IDs (use `YOUR_SPREADSHEET_ID`); real ISINs for held assets (use `US0000000001`/`LU0000000001`/`ES0000000001` family); real portfolio amounts/prices; home directory paths (`/home/agoldhoorn/` → use `~/`). <!-- allow-financial: this line must quote the forbidden pattern -->

**Enforced by a pre-commit hook** (`scripts/check_no_real_financials.py`, first
in `.pre-commit-config.yaml`), because this rule lived only in prose and real
figures reached `main` and GitHub anyway — scrubbed from all 554 commits on
2026-09-04 with `git filter-repo`. The hook favours precision over recall (a
noisy hook gets disabled): it blocks ISINs outside an allowlist, IBANs and
home-directory paths anywhere, plus money-with-cents and six-figure amounts in
**Markdown only** — invented examples are round, and a fixture price of
`155.00` in a test carries no meaning. Put `allow-financial` in a comment on
the line for a genuine exception, so exceptions show up in review.

⚠️ **A history rewrite does not reach commit messages.** `--replace-text` only
rewrites blob contents; the same strings survived in commit messages and needed
a second `--replace-message` pass. Verify both (`git log -p --all` covers
contents *and* messages) before believing a scrub is complete.

**OK:** well-known tickers (AAPL, BTC-EUR) as format examples; Apple's `US0378331005` in prompt templates; personal website/GitHub links in About page; fictional prices in test fixtures.

When writing tests, invent asset names (e.g. "Example Corp", "Global Bond Fund"). Same rule for plan docs under `docs/superpowers/`.
