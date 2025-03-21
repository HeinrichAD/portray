#!/bin/bash
set -euxo pipefail

poetry run cruft check
poetry run mypy --ignore-missing-imports portray/
poetry run ruff check .
poetry run black --check portray/ tests/
poetry run pip-audit
poetry run bandit -r portray/
