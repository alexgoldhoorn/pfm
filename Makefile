# Portfolio Management API - Makefile
# Provides convenient targets for development, testing, and deployment

# Variables
PYTHON := uv run python
ENV_DEV := .env.development
ENV_STAGING := .env.staging
ENV_PROD := .env.production
COMPOSE_FILE := docker-compose.yml
COMPOSE := WEB_PORT=8080 docker compose --profile dev -f $(COMPOSE_FILE)

# Default environment
ENV ?= development

# Color output
GREEN := \033[32m
YELLOW := \033[33m
RED := \033[31m
NC := \033[0m # No Color

.PHONY: help dev test migrate up down clean install lint format check requirements

# Default target
help: ## Show this help message
	@echo "$(GREEN)Portfolio Management API - Available Commands:$(NC)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ { printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""
	@echo "$(GREEN)Usage Examples:$(NC)"
	@echo "  make dev                    # Start development server"
	@echo "  make test                   # Run all tests"
	@echo "  make migrate                # Run database migrations"
	@echo "  make up                     # Start services with docker-compose"
	@echo "  make ENV=staging dev        # Start development server with staging config"

# Development
dev: ## Start development server with auto-reload
	@echo "$(GREEN)Starting development server...$(NC)"
	@if [ -f "$(ENV_DEV)" ]; then \
		echo "$(YELLOW)Loading environment from $(ENV_DEV)$(NC)"; \
		export $$(grep -v '^#' $(ENV_DEV) | xargs) && \
		$(PYTHON) start_server.py --host=localhost --port=8000 --reload; \
	else \
		echo "$(YELLOW)No development environment file found, using defaults$(NC)"; \
		$(PYTHON) start_server.py --host=localhost --port=8000 --reload; \
	fi

dev-staging: ## Start development server with staging configuration
	@echo "$(GREEN)Starting development server with staging configuration...$(NC)"
	@if [ -f "$(ENV_STAGING)" ]; then \
		export $$(grep -v '^#' $(ENV_STAGING) | xargs) && \
		$(PYTHON) start_server.py --host=localhost --port=8000 --reload; \
	else \
		echo "$(RED)Staging environment file not found: $(ENV_STAGING)$(NC)"; \
		exit 1; \
	fi

# Testing
test: ## Run all tests
	@echo "$(GREEN)Running tests...$(NC)"
	@$(PYTHON) -m pytest tests/ -v --tb=short

test-coverage: ## Run tests with coverage report
	@echo "$(GREEN)Running tests with coverage...$(NC)"
	@$(PYTHON) -m pytest tests/ -v --tb=short --cov=portf_manager --cov=portf_server --cov-report=term-missing --cov-report=html

test-unit: ## Run unit tests only
	@echo "$(GREEN)Running unit tests...$(NC)"
	@$(PYTHON) -m pytest tests/ -v --tb=short -k "unit"

test-integration: ## Run integration tests only
	@echo "$(GREEN)Running integration tests...$(NC)"
	@$(PYTHON) -m pytest tests/ -v --tb=short -k "integration"

# Database operations
migrate: ## Run database migrations
	@echo "$(GREEN)Running database migrations...$(NC)"
	@if [ -f "migration_script.py" ]; then \
		$(PYTHON) migration_script.py; \
	else \
		echo "$(RED)Migration script not found: migration_script.py$(NC)"; \
		exit 1; \
	fi

migrate-fresh: ## Run fresh database migration (reset)
	@echo "$(GREEN)Running fresh database migration...$(NC)"
	@if [ -f "script_migrate_fresh.py" ]; then \
		$(PYTHON) script_migrate_fresh.py; \
	else \
		echo "$(RED)Fresh migration script not found: script_migrate_fresh.py$(NC)"; \
		exit 1; \
	fi

migrate-test: ## Test database migrations
	@echo "$(GREEN)Testing database migrations...$(NC)"
	@if [ -f "test_migration.py" ]; then \
		$(PYTHON) test_migration.py; \
	else \
		echo "$(RED)Migration test script not found: test_migration.py$(NC)"; \
		exit 1; \
	fi

# Docker operations
up: ## Start all services with docker-compose
	@echo "$(GREEN)Starting services...$(NC)"
	@$(COMPOSE) up -d
	@echo "$(GREEN)Services started. API at :8000, Web at :8080, PostgreSQL at :5432$(NC)"
	@echo "$(YELLOW)Use 'make down' to stop services$(NC)"

up-build: ## Start services and rebuild containers
	@echo "$(GREEN)Building and starting services...$(NC)"
	@$(COMPOSE) up -d --build

up-backend: ## Rebuild and restart backend only
	@echo "$(GREEN)Rebuilding backend...$(NC)"
	@$(COMPOSE) up -d --build backend

up-web: ## Rebuild and restart web frontend only
	@echo "$(GREEN)Rebuilding web frontend...$(NC)"
	@$(COMPOSE) up -d --build web

down: ## Stop all services
	@echo "$(GREEN)Stopping services...$(NC)"
	@$(COMPOSE) down

logs: ## Show service logs
	@$(COMPOSE) logs -f

logs-backend: ## Show backend logs
	@$(COMPOSE) logs -f backend

restart: ## Restart all services
	@echo "$(GREEN)Restarting services...$(NC)"
	@$(COMPOSE) restart

status: ## Show service status
	@docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "portf|NAMES"

# Environment management
env-dev: ## Copy development environment template
	@if [ ! -f "$(ENV_DEV)" ]; then \
		echo "$(GREEN)Creating development environment file...$(NC)"; \
		cp $(ENV_DEV) $(ENV_DEV).example; \
	else \
		echo "$(YELLOW)Development environment file already exists$(NC)"; \
	fi

env-staging: ## Copy staging environment template
	@if [ ! -f "$(ENV_STAGING)" ]; then \
		echo "$(GREEN)Creating staging environment file...$(NC)"; \
		cp $(ENV_STAGING) $(ENV_STAGING).example; \
	else \
		echo "$(YELLOW)Staging environment file already exists$(NC)"; \
	fi

# Code quality
install: ## Install dependencies (production only)
	@echo "$(GREEN)Installing dependencies...$(NC)"
	@uv sync --no-dev

install-dev: ## Install all dependencies including dev
	@echo "$(GREEN)Installing dependencies (with dev)...$(NC)"
	@uv sync

lint: ## Run linting checks
	@echo "$(GREEN)Running linting checks...$(NC)"
	@$(PYTHON) -m black --check portf_manager/ portf_server/ tests/ || true
	@$(PYTHON) -m isort --check-only portf_manager/ portf_server/ tests/ || true

format: ## Format code with black and isort
	@echo "$(GREEN)Formatting code...$(NC)"
	@$(PYTHON) -m black portf_manager/ portf_server/ tests/
	@$(PYTHON) -m isort portf_manager/ portf_server/ tests/

type-check: ## Run type checking with mypy
	@echo "$(GREEN)Running type checks...$(NC)"
	@$(PYTHON) -m mypy portf_manager/ portf_server/ || true

check: lint type-check ## Run all code quality checks

# Database utilities
db-shell: ## Open database shell (requires running PostgreSQL)
	@echo "$(GREEN)Opening database shell...$(NC)"
	@$(COMPOSE) exec postgres psql -U portf_user -d portf_db

db-backup: ## Create database backup
	@echo "$(GREEN)Creating database backup...$(NC)"
	@$(COMPOSE) exec postgres pg_dump -U portf_user portf_db > backup_$$(date +%Y%m%d_%H%M%S).sql

# Cleanup
clean: ## Clean up temporary files and caches
	@echo "$(GREEN)Cleaning up...$(NC)"
	@find . -type f -name "*.pyc" -delete
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache/
	@rm -rf htmlcov/
	@rm -rf .coverage
	@echo "$(GREEN)Cleanup complete$(NC)"

clean-docker: ## Clean up Docker containers and volumes
	@echo "$(GREEN)Cleaning up Docker resources...$(NC)"
	@$(COMPOSE) down -v --remove-orphans
	@docker system prune -f

# Requirements export (for tools that still need requirements.txt)
requirements: ## Export requirements.txt from uv lockfile
	@echo "$(GREEN)Exporting requirements.txt from uv lockfile...$(NC)"
	@uv export --format requirements-txt --no-dev > requirements.txt

# Server utilities
server: ## Start production server
	@echo "$(GREEN)Starting production server...$(NC)"
	@if [ -f "$(ENV_PROD)" ]; then \
		export $$(grep -v '^#' $(ENV_PROD) | xargs) && \
		$(PYTHON) start_server.py --host=0.0.0.0 --port=8000 --workers=4; \
	else \
		echo "$(YELLOW)No production environment file found, using defaults$(NC)"; \
		$(PYTHON) start_server.py --host=0.0.0.0 --port=8000 --workers=4; \
	fi

# Configuration utilities
config-test: ## Test and validate current configuration
	@echo "$(GREEN)Testing configuration...$(NC)"
	@$(PYTHON) config_test.py

config-test-dev: ## Test development configuration
	@echo "$(GREEN)Testing development configuration...$(NC)"
	@$(PYTHON) config_test.py --env=development

config-test-staging: ## Test staging configuration
	@echo "$(GREEN)Testing staging configuration...$(NC)"
	@$(PYTHON) config_test.py --env=staging

config-test-prod: ## Test production configuration
	@echo "$(GREEN)Testing production configuration...$(NC)"
	@$(PYTHON) config_test.py --env=production
