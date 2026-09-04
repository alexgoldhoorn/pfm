#!/usr/bin/env python3
"""Pre-commit guard against committing real personal financial data.

This is a public repo. ``CLAUDE.md``'s privacy section forbids real portfolio
amounts, real ISINs for held assets, and home-directory paths — but that rule
lived only in prose, and real figures reached ``main`` and GitHub anyway
(scrubbed from history 2026-09-04). This hook makes the rule mechanical.

It deliberately favours precision over recall: a noisy hook gets disabled, and
a hook that fires on every legitimate example teaches people to pass
``--no-verify``. So it only flags patterns that are almost never a made-up
example:

- **Money with cents, in Markdown.** Invented figures in prose are round
  ("€1,000", "€50k"); ``€1,887.70`` is copied from a statement. Code and tests
  are exempt — a fixture price of ``155.00`` is normal and carries no meaning.
- **Six-figure amounts, in Markdown.** Net-worth-scale numbers.
- **ISINs outside an allowlist**, anywhere. An ISIN names a specific security,
  so a real one is a disclosed holding.
- **IBANs and home-directory paths**, anywhere.

To allow a genuine exception, put ``allow-financial`` in a comment on the same
line.

Exit code 1 blocks the commit and prints file:line for each hit.
"""

import re
import sys
from pathlib import Path

# An escape hatch on the offending line itself, so exceptions are visible in
# review rather than hidden in a config file.
ALLOW_MARKER = "allow-financial"

# Placeholder ISINs: a country code followed by at least four zeros. Covers the
# US0000000001/LU0000000001/ES0000000001 family CLAUDE.md prescribes, and the
# placeholders substituted during the 2026-09-04 history scrub.
PLACEHOLDER_ISIN = re.compile(r"^[A-Z]{2}0{4}")

# Real ISINs that are explicitly fine: Apple appears in prompt templates per
# CLAUDE.md, and these two funds are widely-used documentation examples that
# are not held.
ALLOWED_ISINS = {
    "US0378331005",  # Apple — sanctioned by CLAUDE.md for prompt templates
    "IE00B3XXRP09",  # Vanguard FTSE All-World — doc example, not held
    "IE00B4L5Y983",  # iShares Core MSCI World — doc example, not held
}

ISIN = re.compile(r"\b[A-Z]{2}[0-9A-Z]{9}[0-9]\b")
IBAN = re.compile(r"\b[A-Z]{2}[0-9]{2}[ ]?(?:[0-9]{4}[ ]?){3,7}[0-9]{1,4}\b")
HOME_PATH = re.compile(r"/home/([a-z][a-z0-9_-]*)/")

# Usernames that are obviously stand-ins, or belong to a container rather than
# to a person, so they leak nothing.
GENERIC_USERS = {"you", "youruser", "user", "username", "appuser", "someone"}

# Markdown-only money rules.
MONEY_WITH_CENTS = re.compile(r"(?:€|EUR\s?)\d{1,3}(?:,\d{3})*\.\d{2}")
MONEY_SIX_FIGURES = re.compile(r"(?:€|EUR\s?)\d{3},\d{3}\b")

# "€0.00" is a placeholder for "nothing", never a disclosed balance.
CENTS_EXEMPT = {"€0.00", "EUR 0.00", "EUR0.00"}

TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".js",
    ".mjs",
    ".html",
    ".css",
    ".json",
    ".yaml",
    ".yml",
    ".txt",
    ".sh",
    ".toml",
    ".cfg",
    ".ini",
}


def check_line(path: Path, line: str) -> list[str]:
    """Return a list of problem descriptions for one line."""
    if ALLOW_MARKER in line:
        return []
    problems = []

    for isin in ISIN.findall(line):
        if isin in ALLOWED_ISINS or PLACEHOLDER_ISIN.match(isin):
            continue
        problems.append(
            f"possible real ISIN {isin!r} — an ISIN names a specific security, "
            f"so a real one discloses a holding"
        )

    for iban in IBAN.findall(line):
        # An ISIN also matches the loose IBAN shape; don't report it twice.
        if ISIN.fullmatch(iban.replace(" ", "")):
            continue
        problems.append(f"possible IBAN/account number {iban!r}")

    for user in HOME_PATH.findall(line):
        if user in GENERIC_USERS:
            continue
        problems.append(f"home-directory path '/home/{user}/' — use ~/ instead")

    if path.suffix == ".md":
        for amount in MONEY_WITH_CENTS.findall(line):
            if amount.strip() in CENTS_EXEMPT:
                continue
            problems.append(
                f"money with cents {amount!r} — invented examples are round; "
                f"this looks copied from a statement"
            )
        for amount in MONEY_SIX_FIGURES.findall(line):
            problems.append(f"six-figure amount {amount!r}")

    return problems


def main(argv: list[str]) -> int:
    failures = 0
    for name in argv:
        path = Path(name)
        if path.suffix not in TEXT_SUFFIXES or not path.is_file():
            continue
        # This file necessarily contains the patterns it hunts for.
        if path.name == Path(__file__).name:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, start=1):
            for problem in check_line(path, line):
                print(f"{path}:{number}: {problem}")
                failures += 1

    if failures:
        print(
            f"\n{failures} possible leak(s) of real financial data blocked.\n"
            f"This is a public repo — see the Privacy section of CLAUDE.md.\n"
            f"If a hit is a genuine example, add '{ALLOW_MARKER}' in a comment "
            f"on that line."
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
