#!/usr/bin/env python3
"""Back-compat shim: ``from pfm.server import <tool>`` and ``python3 server.py``.

The server lives in the ``pfm_mcp`` package next to this file. Other hosts
reach this file through a symlink (``~/mcp/pfm/server.py``), so the real
directory is put on ``sys.path`` (appended: only ``pfm_mcp`` lives there).
"""

import os
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.append(_HERE)

from pfm_mcp.server import main, mcp  # noqa: E402,F401
from pfm_mcp.tools.allocate import *  # noqa: E402,F401,F403
from pfm_mcp.tools.analytics import *  # noqa: E402,F401,F403
from pfm_mcp.tools.networth import *  # noqa: E402,F401,F403
from pfm_mcp.tools.portfolio import *  # noqa: E402,F401,F403
from pfm_mcp.tools.research import *  # noqa: E402,F401,F403
from pfm_mcp.tools.spending import *  # noqa: E402,F401,F403
from pfm_mcp.tools.tax import *  # noqa: E402,F401,F403

if __name__ == "__main__":
    main()
