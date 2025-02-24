.PHONY: help install dev test lint format clean build run deploy

# Display help information
help:
	@echo "RAG Service Development Commands"
	@echo "--------------------------------"
	@echo "make install    - Install dependencies"
	@echo "make dev        - Start development environment"
	@echo "make test       - Run tests"
	@echo "make lint       - Run linting"
	@echo "make format     - Format code"
	@echo "make clean      - Clean build artifacts"
	@echo "make build      - Build Docker image"
	@echo "make run        - Run service locally"
	@echo "make deploy     - Deploy service"

# Install dependencies
install:
	pip install poetry
	poetry install

# Start development environment
dev:
	docker-compose -f docker-compose.dev.yml up -d
	poetry run uvicorn rag_service.app:app --reload --host 0.0.0.0 --port 8000

# Run tests
test:
	poetry run pytest tests/ -v --cov=rag_service --cov-report=term-missing

# Run specific test categories
test-unit:
	poetry run pytest tests/unit/ -v

test-integration:
	poetry run pytest tests/integration/ -v

test-e2e:
	poetry run pytest tests/e2e/ -v

# Run linting
lint:
	poetry run flake8 rag_service tests
	poetry run mypy rag_service tests
	poetry run bandit -r rag_service

# Format code
format:
	poetry run black rag_service tests
	poetry run isort rag_service tests

# Clean build artifacts
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache
	rm -rf .coverage
	rm -rf htmlcov
	rm -rf .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} +

# Build Docker image
build:
	docker build -t rag-service:latest .

# Run service locally
run:
	poetry run python -m rag_service.app

# Deploy service
deploy:
	docker-compose up -d

# Development utilities
setup-dev: install
	pre-commit install
	mkdir -p logs
	cp .env.template .env

update-deps:
	poetry update

check-security:
	poetry run safety check
	poetry run bandit -r rag_service

generate-docs:
	poetry run pdoc --html --output-dir docs/ rag_service

# Database operations
db-init:
	poetry run python scripts/init_db.py

db-migrate:
	poetry run python scripts/migrate_db.py

db-seed:
	poetry run python scripts/seed_db.py

# Monitoring operations
start-monitoring:
	docker-compose up -d prometheus grafana

view-logs:
	docker-compose logs -f

# Cleanup operations
cleanup-dev:
	docker-compose -f docker-compose.dev.yml down -v
	rm -rf logs/*
	rm -rf data/*

cleanup-all: cleanup-dev clean
	docker system prune -f

# Quality checks
quality-check: lint test check-security

# CI/CD operations
ci-build:
	docker build --target builder -t rag-service:ci .

ci-test: ci-build
	docker run --rm rag-service:ci poetry run pytest

ci-quality: ci-build
	docker run --rm rag-service:ci make quality-check 