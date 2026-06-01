#!/usr/bin/env python3
"""
Safe F541 fixer: removes f-prefix from f-strings that have no placeholders.

SAFE: uses negative lookahead (?!"") to exclude triple-quoted strings.
Never match across f followed by triple-quote — f"" matches the first two
chars of f-triple-quote and strips the f from multiline f-strings.
"""

import re
import pathlib
import sys

# Only match SINGLE-LINE f-strings without {} placeholders.
# Negative lookahead (?!"") ensures we skip triple-quoted openers (f""").
PATTERN = re.compile(r'\bf("(?!"")[^"{}\n]*"|\'(?!\')[^\'{}\\n]*\')')


def fix_file(path: pathlib.Path) -> bool:
    txt = path.read_text()
    new = PATTERN.sub(lambda m: m.group(0)[1:], txt)
    if new != txt:
        path.write_text(new)
        return True
    return False


if __name__ == "__main__":
    dirs = sys.argv[1:] or ["portf_manager", "portf_server"]
    fixed = 0
    for d in dirs:
        for p in pathlib.Path(d).rglob("*.py"):
            if fix_file(p):
                fixed += 1
                print(f"Fixed: {p}")
    print(f"\n{fixed} files fixed.")
