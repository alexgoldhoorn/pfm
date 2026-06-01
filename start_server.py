#!/usr/bin/env python3
"""
Startup script for Portfolio Management FastAPI server.

Usage:
    python start_server.py [--port PORT] [--host HOST] [--env ENV]
"""

import argparse
import os
import uvicorn
from dotenv import load_dotenv
from portf_server.settings import get_settings


def main():
    # Load environment variables from .env files
    load_dotenv(".env.local", override=False)
    load_dotenv(".env", override=False)

    parser = argparse.ArgumentParser(
        description="Start Portfolio Management API server"
    )
    parser.add_argument("--host", help="Host to bind to (overrides settings)")
    parser.add_argument("--port", type=int, help="Port to bind to (overrides settings)")
    parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload for development"
    )
    parser.add_argument(
        "--workers", type=int, help="Number of worker processes (overrides settings)"
    )
    parser.add_argument(
        "--env",
        choices=["development", "staging", "production"],
        help="Environment to use for configuration",
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Log level (overrides settings)",
    )

    args = parser.parse_args()

    # Set environment-specific .env file if specified
    if args.env:
        env_file = f".env.{args.env}"
        if os.path.exists(env_file):
            print(f"Loading environment from {env_file}")
            os.environ["PORTF_ENVIRONMENT"] = args.env
        else:
            print(f"Warning: Environment file {env_file} not found")

    # Get settings (will load from environment/files)
    settings = get_settings()

    # Use command line arguments or fall back to settings
    host = args.host or settings.host
    port = args.port or settings.port
    workers = args.workers or settings.workers
    reload = args.reload or settings.reload
    log_level = args.log_level or settings.log_level

    print(f"Starting Portfolio Management API server on {host}:{port}")
    print(f"Environment: {settings.environment}")
    print(f"OpenAPI documentation will be available at: http://{host}:{port}/docs")
    print("Press Ctrl+C to stop the server")

    uvicorn.run(
        "portf_server.app:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,  # Can't use workers with reload
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
