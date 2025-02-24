# Contributing to RAG Service

Thank you for your interest in contributing to the RAG Service project! This document provides guidelines and instructions for contributing.

## Table of Contents
- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)
- [Code Style](#code-style)
- [Documentation](#documentation)
- [Issue Reporting](#issue-reporting)
- [Feature Requests](#feature-requests)

## Code of Conduct

This project adheres to a Code of Conduct that all contributors are expected to follow. Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before contributing.

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/your-username/rag-service.git
   cd rag-service
   ```
3. Add the upstream repository:
   ```bash
   git remote add upstream https://github.com/original-owner/rag-service.git
   ```

## Development Setup

1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install poetry
   poetry install
   ```

3. Set up pre-commit hooks:
   ```bash
   pre-commit install
   ```

4. Copy environment template:
   ```bash
   cp .env.template .env
   ```
   Then edit `.env` with your configuration.

5. Start development services:
   ```bash
   docker-compose -f docker-compose.dev.yml up -d
   ```

## Making Changes

1. Create a new branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes following our [code style guidelines](#code-style)

3. Write or update tests as needed

4. Run the test suite:
   ```bash
   pytest
   ```

5. Commit your changes:
   ```bash
   git add .
   git commit -m "feat: add your feature description"
   ```
   Please follow [Conventional Commits](https://www.conventionalcommits.org/) specification.

## Testing

- Write unit tests for all new functionality
- Ensure all tests pass before submitting changes
- Run the full test suite:
  ```bash
  pytest
  ```
- Run linting:
  ```bash
  flake8
  black .
  isort .
  ```

## Submitting Changes

1. Push your changes to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

2. Create a Pull Request (PR) from your fork to our main repository

3. Fill out the PR template completely

4. Wait for review and address any feedback

## Code Style

We follow these style guides:
- [PEP 8](https://www.python.org/dev/peps/pep-0008/)
- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)

Additional requirements:
- Use type hints
- Write docstrings for all public functions and classes
- Keep functions focused and small
- Use meaningful variable names
- Comment complex logic
- Use f-strings for string formatting

## Documentation

- Update documentation for any changed functionality
- Write clear commit messages
- Include docstrings for new functions and classes
- Update README.md if needed
- Add comments for complex logic

## Issue Reporting

When reporting issues:
1. Use the issue template
2. Include steps to reproduce
3. Include expected vs actual behavior
4. Include system information
5. Include relevant logs
6. Include screenshots if applicable

## Feature Requests

When requesting features:
1. Use the feature request template
2. Explain the use case
3. Describe expected behavior
4. Provide examples if possible
5. Consider alternatives

## Additional Resources

- [Project README](README.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [License](LICENSE)
- [Security Policy](SECURITY.md)

## Questions?

If you have questions, please:
1. Check existing issues
2. Check the documentation
3. Open a new issue if needed

Thank you for contributing to RAG Service! 