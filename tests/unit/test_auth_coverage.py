"""Guard test: every data endpoint must require API key authentication.

This walks the live FastAPI route table (the same technique the security audit
used to find the open endpoints) and asserts that any route not on an explicit
public allowlist carries an auth dependency in its dependency tree. It exists to
permanently prevent the "shipped an endpoint without auth" regression — adding a
new data router without ``dependencies=[Depends(require_api_key_dep)]`` (see
``portf_server/app.py``) fails this test.
"""

from fastapi.routing import APIRoute

from portf_server.app import app

# Paths that are intentionally reachable without an API key:
# - infra/docs endpoints
# - the auth surface itself (login/register)
# - the public %-only summary (separately gated by PORTF_PUBLIC_VIEW)
PUBLIC_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/public",
)
PUBLIC_EXACT = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
}


def _route_has_auth(route: APIRoute) -> bool:
    """True if any dependency in the route's tree enforces API key auth."""
    names = []
    for dep in route.dependant.dependencies:
        if dep.call is not None:
            names.append(dep.call.__name__)
        # one level down covers per-endpoint helper deps that wrap require_api_key
        for sub in dep.dependencies:
            if sub.call is not None:
                names.append(sub.call.__name__)
    blob = " ".join(names).lower()
    return any(k in blob for k in ("api_key", "auth", "current_user"))


def _is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


def test_all_data_endpoints_require_auth():
    """No data endpoint may be served without authentication."""
    unprotected = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if _is_public(route.path):
            continue
        if not _route_has_auth(route):
            method = sorted(route.methods - {"HEAD", "OPTIONS"})
            unprotected.append(f"{method} {route.path}")

    assert not unprotected, (
        "These endpoints are reachable without an API key — add the router-level "
        "dependency in app.py (or add the path to the public allowlist if that is "
        "intended):\n  " + "\n  ".join(sorted(unprotected))
    )


def test_public_allowlist_routes_exist():
    """Sanity: the allowlisted public routes are actually mounted (so the
    allowlist can't silently rot into covering nothing)."""
    paths = {r.path for r in app.routes if isinstance(r, APIRoute)}
    assert "/api/v1/auth/login-key" in paths
    assert "/health" in paths
