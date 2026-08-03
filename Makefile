PYTHON_LINT_TARGETS = tools-harness/*.py tests test_refactor.py

.PHONY: lint lint-python lint-js

lint: lint-python lint-js

lint-python:
	ruff check $(PYTHON_LINT_TARGETS)

lint-js:
	npm --prefix google-mcp run lint

.PHONY: eval eval-live

# Tier 0 + 1: deterministic, offline, seconds. Safe on every commit.
eval:
	pytest tools-harness/evals -m "not live" -q

# Tier 2: real models, real tokens, minutes. Never in CI.
eval-live:
	pytest tools-harness/evals -m live -q -s

.PHONY: clean

clean:
	rm -f tools-harness/*.log tools-harness/**/*.log
	rm -rf .gstack/ .playwright-mcp/
	rm -rf .claude/worktrees/*
