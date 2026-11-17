"""Tests for the shared HTTP helper."""

import io
from unittest.mock import patch

from pfm_mcp import client


def test_get_encodes_query_values():
    with patch(
        "pfm_mcp.client.urllib.request.urlopen",
        return_value=io.BytesIO(b"[]"),
    ) as urlopen:
        client._get("/x", {"symbols": "NVDA, ASML.AS", "q": "a&b#c", "skip": None})
    url = urlopen.call_args.args[0].full_url
    assert url.endswith("/x?symbols=NVDA%2C%20ASML.AS&q=a%26b%23c")
