"""Small helpers shared by the tool tests."""


def reply(payload):
    """Stand in for the JSON body of an API reply (``_get`` returns it parsed)."""
    return payload


def requested_url(mock_get) -> str:
    """Rebuild the request URL from the last call to a patched ``_get``.

    Args:
        mock_get: The patched ``pfm_mcp.client._get``.

    Returns:
        ``path?query`` built with the same rule ``_get`` uses.
    """
    args, kwargs = mock_get.call_args
    path = args[0]
    params = args[1] if len(args) > 1 else kwargs.get("params")
    query = "&".join(f"{k}={v}" for k, v in (params or {}).items() if v is not None)
    return f"{path}?{query}" if query else path
