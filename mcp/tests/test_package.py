"""Package-level checks: tool count, shim, no dependency on pfm internals."""

import os
import re
import subprocess
import sys
from pathlib import Path

from pfm_mcp.server import mcp

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TOOLS = 32


def test_registers_every_tool():
    assert len(mcp._tool_manager.list_tools()) == EXPECTED_TOOLS


def test_package_does_not_import_pfm_internals():
    for path in (ROOT / "pfm_mcp").rglob("*.py"):
        source = path.read_text()
        assert not re.search(r"^\s*(from|import) portf_(manager|server)", source, re.M)
        assert "sys.path" not in source


def test_shim_works_through_a_symlink(tmp_path):
    (tmp_path / "pfm").mkdir()
    (tmp_path / "pfm" / "server.py").symlink_to(ROOT / "server.py")
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from pfm.server import networth, next_euro, wash_sale_check, mcp\n"
        "print(len(mcp._tool_manager.list_tools()))"
    ) % str(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(EXPECTED_TOOLS)
