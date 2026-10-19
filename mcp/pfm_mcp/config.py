"""Where the server finds the pfm API: URL and API key.

Resolution order, first hit wins (URL and key resolve independently):

1. ``PFM_URL`` / ``PFM_API_KEY`` environment variables;
2. a dotenv file named by ``PFM_ENV_FILE`` (keys ``PORTF_SERVER_URL``,
   ``SERVER_API_KEY``);
3. the legacy ``~/repos/pfm/.env.local``, if it exists;
4. ``http://127.0.0.1:8000`` and an empty key.
"""

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping, Optional

DEFAULT_URL = "http://127.0.0.1:8000"
LEGACY_ENV_FILE = "~/repos/pfm/.env.local"


@dataclass(frozen=True)
class Settings:
    """Resolved connection settings."""

    url: str
    api_key: str


def read_env_file(path: str) -> dict[str, str]:
    """Parse a simple ``KEY=VALUE`` dotenv file.

    Args:
        path: File path (``~`` is expanded).

    Returns:
        The keys found; empty when the file is missing or unreadable.
    """
    values: dict[str, str] = {}
    try:
        with open(os.path.expanduser(path)) as handle:
            for line in handle:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def resolve(
    environ: Optional[Mapping[str, str]] = None,
    legacy_path: str = LEGACY_ENV_FILE,
) -> Settings:
    """Resolve the settings from the environment, an env file or the defaults.

    Args:
        environ: Environment mapping (defaults to ``os.environ``).
        legacy_path: Path of the legacy owner's env file.

    Returns:
        The resolved settings.
    """
    env = os.environ if environ is None else environ
    named = env.get("PFM_ENV_FILE")
    file_values = read_env_file(named) if named else {}
    legacy = read_env_file(legacy_path)
    url = (
        env.get("PFM_URL")
        or file_values.get("PORTF_SERVER_URL")
        or legacy.get("PORTF_SERVER_URL")
        or DEFAULT_URL
    )
    key = (
        env.get("PFM_API_KEY")
        or file_values.get("SERVER_API_KEY")
        or legacy.get("SERVER_API_KEY")
        or ""
    )
    return Settings(url=url, api_key=key)


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Return the settings, resolved once; warn on stderr when the key is empty.

    Returns:
        The process-wide settings.
    """
    resolved = resolve()
    if not resolved.api_key:
        # stderr only: some hosts deliver stdout as a message.
        print(
            "pfm-mcp: no API key set (PFM_API_KEY); requests will be rejected",
            file=sys.stderr,
        )
    return resolved
