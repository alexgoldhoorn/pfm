"""
Configuration management for Portfolio Manager.

Provides centralized configuration and mode detection for local SQLite vs server modes.
"""

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


@dataclass
class PortfolioConfig:
    """Configuration for portfolio manager operation mode."""

    # Server mode configuration
    server_url: Optional[str] = None
    api_key: Optional[str] = None

    # Local mode configuration
    db_path: str = "portfolio.db"

    # Debug and other options
    debug: bool = False

    @property
    def is_server_mode(self) -> bool:
        """Check if running in server mode."""
        return bool(self.server_url)

    @property
    def is_local_mode(self) -> bool:
        """Check if running in local mode."""
        return not self.is_server_mode

    def validate(self) -> None:
        """Validate configuration settings."""
        if self.is_server_mode:
            if not self.server_url:
                raise ValueError("Server URL is required in server mode")

            # Parse and validate server URL
            try:
                parsed = urlparse(self.server_url)
                if not parsed.scheme or not parsed.netloc:
                    raise ValueError("Invalid server URL format")
            except Exception as e:
                raise ValueError(f"Invalid server URL: {e}")

            if not self.api_key:
                raise ValueError("API key is required in server mode")

    @classmethod
    def from_args_and_env(
        cls,
        server_url: Optional[str] = None,
        api_key: Optional[str] = None,
        db_path: Optional[str] = None,
        debug: bool = False,
    ) -> "PortfolioConfig":
        """
        Create configuration from command arguments and environment variables.

        Args:
            server_url: Server URL from command line
            api_key: API key from command line
            db_path: Database path from command line
            debug: Debug mode flag

        Returns:
            PortfolioConfig: Configured instance
        """
        # Use environment variables as fallbacks
        final_server_url = server_url or os.getenv("PORTF_SERVER_URL")
        final_api_key = api_key or os.getenv("PORTF_API_KEY")
        final_db_path = db_path or os.getenv("PORTF_DB_PATH", "portfolio.db")

        config = cls(
            server_url=final_server_url,
            api_key=final_api_key,
            db_path=final_db_path,
            debug=debug,
        )

        config.validate()
        return config


# Global configuration instance
_config: Optional[PortfolioConfig] = None


def get_config() -> Optional[PortfolioConfig]:
    """Get the current global configuration."""
    return _config


def set_config(config: PortfolioConfig) -> None:
    """Set the global configuration."""
    global _config
    _config = config


def is_server_mode() -> bool:
    """Check if currently running in server mode."""
    config = get_config()
    return config.is_server_mode if config else False


def is_local_mode() -> bool:
    """Check if currently running in local mode."""
    config = get_config()
    return config.is_local_mode if config else True
