#!/bin/bash
set -euxo pipefail

poetry run cruft check
poetry run mypy --ignore-missing-imports portray/
poetry run ruff .
poetry run black --check portray/ tests/
poetry run safety check
poetry run bandit -r portray/
