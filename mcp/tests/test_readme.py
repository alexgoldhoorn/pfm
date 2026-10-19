"""The README's tool table must list exactly the registered tools."""

import re
from pathlib import Path

from pfm_mcp.server import mcp

README = Path(__file__).resolve().parent.parent / "README.md"


def test_readme_tool_table_matches_registered_tools():
    section = README.read_text().split("\n## Tools\n", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"^\| `(\w+)` \|", section, re.M))
    registered = {t.name for t in mcp._tool_manager.list_tools()}
    assert documented == registered
