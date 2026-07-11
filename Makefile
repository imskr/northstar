.PHONY: install dev test lint init-db

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

dev:
	.venv/bin/python scripts/dev.py

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check northstar tests scripts

init-db:
	.venv/bin/python scripts/init_db.py
