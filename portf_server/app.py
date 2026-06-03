"""
FastAPI Server for Portfolio Management System

This module provides the main FastAPI application with mounted routers for all domains:
- Assets: Asset management and price data
- Transactions: Transaction recording and retrieval
- Portfolios: Portfolio management and analysis
- Entities: Broker and platform management
- Sectors: Sector classification and analysis
- Auth: User authentication and authorization
- LLM: AI-powered transaction extraction
- Tax: Tax calculation and reporting
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from portf_manager.database import Database
from portf_manager.auth import AuthManager
from .auth_middleware import APIKeyManager
from .settings import get_settings

from .routers import (
    assets,
    transactions,
    portfolios,
    entities,
    sectors,
    auth,
    llm,
    tax,
    imports,
    exports,
    bookings,
    sync,
    rebalance,
    research,
    analytics,
    watchlist,
    goals,
    public,
    networth,
)
from .dependencies import get_database, get_auth_manager, get_api_key_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global database, auth manager, and API key manager instances
database = None
auth_manager = None
api_key_manager = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager for startup and shutdown tasks.
    """
    global database, auth_manager, api_key_manager

    # Get settings
    settings = get_settings()

    # Configure logging based on settings
    log_level = getattr(logging, settings.log_level.upper())
    logging.getLogger().setLevel(log_level)

    # Startup
    logger.info(
        f"Starting Portfolio Management API server (Environment: {settings.environment})..."
    )

    # Initialize database
    try:
        # Extract database path/URL from settings
        if settings.database_url.startswith("sqlite"):
            # Extract path from sqlite URL
            db_path = settings.database_url.replace("sqlite:///", "").replace(
                "sqlite://", ""
            )
            database = Database(db_path)
        else:
            # For PostgreSQL and other databases, we'd need to update Database class
            # For now, fall back to default
            database = Database("portfolio.db")
        logger.info(f"Database initialized successfully ({settings.database_url})")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

    # Initialize auth manager
    try:
        auth_manager = AuthManager(database)
        logger.info("Authentication manager initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize auth manager: {e}")
        raise

    # Initialize API key manager
    try:
        api_key_manager = APIKeyManager(database)
        logger.info("API key manager initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize API key manager: {e}")
        raise

    # Seed SERVER_API_KEY from env if set and not already registered
    import os as _os

    _env_key = _os.getenv("SERVER_API_KEY")
    if _env_key:
        try:
            existing = api_key_manager.validate_api_key(_env_key)
            if not existing:
                api_key_manager.create_api_key(
                    key_name="server-env-key",
                    description="Auto-seeded from SERVER_API_KEY env var",
                    raw_key=_env_key,
                )
                logger.info("SERVER_API_KEY seeded into api_keys table")
            else:
                logger.debug("SERVER_API_KEY already registered")
        except Exception as e:
            logger.warning(f"Could not seed SERVER_API_KEY: {e}")

    logger.info("Portfolio Management API server started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Portfolio Management API server...")
    database = None
    auth_manager = None
    api_key_manager = None


# Get settings for app configuration
settings = get_settings()

# Create FastAPI application
app = FastAPI(
    title=settings.title,
    description=settings.description,
    version=settings.version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler for unhandled exceptions.
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred. Please try again later.",
        },
    )


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint to verify the API is running.
    """
    return {
        "status": "healthy",
        "message": "Portfolio Management API is running",
        "version": settings.version,
        "environment": settings.environment,
    }


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """
    Root endpoint with API information.
    """
    return {
        "message": f"Welcome to {settings.title}",
        "version": settings.version,
        "environment": settings.environment,
        "documentation": "/docs",
        "openapi": "/openapi.json",
    }


# Mount all domain routers
app.include_router(
    auth.router,
    prefix="/api/v1/auth",
    tags=["Authentication"],
)

app.include_router(
    assets.router,
    prefix="/api/v1/assets",
    tags=["Assets"],
)

app.include_router(
    transactions.router,
    prefix="/api/v1/transactions",
    tags=["Transactions"],
)

app.include_router(
    portfolios.router,
    prefix="/api/v1/portfolios",
    tags=["Portfolios"],
)

app.include_router(
    entities.router,
    prefix="/api/v1/entities",
    tags=["Entities"],
)

app.include_router(
    sectors.router,
    prefix="/api/v1/sectors",
    tags=["Sectors"],
)

app.include_router(
    llm.router,
    prefix="/api/v1/llm",
    tags=["LLM"],
)

app.include_router(
    tax.router,
    prefix="/api/v1/tax",
    tags=["Tax"],
)

app.include_router(
    imports.router,
    prefix="/api/v1/import",
    tags=["Import"],
)

app.include_router(
    exports.router,
    prefix="/api/v1/export",
    tags=["Export"],
)

app.include_router(
    bookings.router,
    prefix="/api/v1/bookings",
    tags=["Bookings"],
)

app.include_router(
    sync.router,
    prefix="/api/v1/sync",
    tags=["Sync"],
)

app.include_router(
    rebalance.router,
    prefix="/api/v1/rebalance",
    tags=["Rebalance"],
)

app.include_router(
    research.router,
    prefix="/api/v1/research",
    tags=["Research"],
)

app.include_router(
    analytics.router,
    prefix="/api/v1/analytics",
    tags=["Analytics"],
)

app.include_router(
    watchlist.router,
    prefix="/api/v1/watchlist",
    tags=["Watchlist"],
)

app.include_router(
    goals.router,
    prefix="/api/v1/goals",
    tags=["Goals"],
)

app.include_router(
    public.router,
    prefix="/api/v1/public",
    tags=["Public"],
)

app.include_router(
    networth.router,
    prefix="/api/v1/networth",
    tags=["Net Worth"],
)


# Dependency injection for database, auth manager, and API key manager
app.dependency_overrides[get_database] = lambda: database
app.dependency_overrides[get_auth_manager] = lambda: auth_manager
app.dependency_overrides[get_api_key_manager] = lambda: api_key_manager


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="localhost",
        port=8000,
        reload=True,
        log_level="info",
    )
