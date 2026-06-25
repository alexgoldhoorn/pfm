"""
Server configuration using Pydantic BaseSettings.

Supports configuration from environment variables, .env files, and settings.toml.
"""

from typing import List, Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    """Server configuration settings."""

    model_config = SettingsConfigDict(
        env_file=[".env.local", ".env"],
        env_file_encoding="utf-8",
        env_prefix="PORTF_",
        case_sensitive=False,
        extra="ignore",  # Ignore extra environment variables
        # Note: toml_file requires pydantic-settings[toml] extra
        # For now, we'll use environment variables and .env files
    )

    # Server configuration
    host: str = Field(default="localhost", description="Host to bind the server to")
    port: int = Field(default=8000, description="Port to bind the server to")
    debug: bool = Field(default=False, description="Enable debug mode")
    reload: bool = Field(
        default=False, description="Enable auto-reload for development"
    )
    workers: int = Field(default=1, description="Number of worker processes")
    log_level: Literal["critical", "error", "warning", "info", "debug", "trace"] = (
        Field(default="info", description="Log level")
    )

    # Database configuration
    database_url: str = Field(
        default="sqlite:///portfolio.db",
        description="Database connection URL",
    )

    # Environment configuration
    environment: Literal["development", "staging", "production"] = Field(
        default="development", description="Application environment"
    )

    # Security settings
    secret_key: str = Field(
        default="your-secret-key-change-in-production",
        description="Secret key for JWT tokens and other cryptographic operations",
    )
    api_key_header: str = Field(
        default="X-API-Key", description="Header name for API key authentication"
    )
    # Self-service account creation. Off by default: /login-key hands out the
    # shared SERVER_API_KEY to any valid login, so open registration would let
    # anyone provision full access. The demo deployment sets this true.
    allow_registration: bool = Field(
        default=False,
        description="Allow self-service user registration (PORTF_ALLOW_REGISTRATION)",
    )

    # CORS settings
    cors_origins: List[str] = Field(
        default=[
            "http://localhost:3000",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        ],
        description="Allowed CORS origins",
    )

    # Application metadata
    title: str = Field(default="Portfolio Management API", description="API title")
    description: str = Field(
        default="A comprehensive API for portfolio management, transaction tracking, and tax reporting",
        description="API description",
    )
    version: str = Field(default="2.5.7", description="API version")

    # External service configuration
    gemini_api_key: Optional[str] = Field(
        default=None, description="Google Gemini API key for LLM features"
    )
    openrouter_api_key: Optional[str] = Field(
        default=None, description="OpenRouter API key for LLM features"
    )

    # LLM configuration
    # Maps to PORTF_LLM_PROVIDER and PORTF_LLM_MODEL env vars, which llm_client.py reads directly.
    llm_provider: Optional[str] = Field(
        default=None,
        description="LLM provider: auto (default), ollama, gemini, openrouter",
    )
    llm_model: Optional[str] = Field(
        default=None,
        description=(
            "LLM model name. Defaults per provider: "
            "gemini=gemini-2.5-flash, ollama=llama3.2, openrouter=openai/gpt-4o-mini"
        ),
    )

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.environment == "development"

    @property
    def is_staging(self) -> bool:
        """Check if running in staging environment."""
        return self.environment == "staging"

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment == "production"


# Global settings instance
_settings: Optional[ServerSettings] = None


def get_settings() -> ServerSettings:
    """Get or create the global settings instance."""
    global _settings
    if _settings is None:
        _settings = ServerSettings()
    return _settings


def reload_settings() -> ServerSettings:
    """Reload settings from configuration sources."""
    global _settings
    _settings = ServerSettings()
    return _settings
