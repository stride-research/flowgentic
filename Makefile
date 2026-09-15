.PHONY: help install demo-v1 demo-v2 clean
.DEFAULT_GOAL := help

BLUE  = \033[34m
GREEN = \033[32m
DIM   = \033[2m
RESET = \033[0m

help:
	@echo "$(BLUE)flowgentic — CNIO naive pipeline$(RESET)"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  $(GREEN)%-12s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""

install: ## Create the venv and install the package
	uv sync

demo-v1: install ## Show the v1 architecture sketch (pseudocode, does not execute)
	@echo "$(DIM)v1 is a non-executable architecture sketch: it names the shape$(RESET)"
	@echo "$(DIM)we are building toward, not something that runs.$(RESET)"
	@echo ""
	@uv run python -c "import ast; \
	print(ast.get_docstring(ast.parse(open('src/flowgentic/demos/cnio/v1.py').read())) or '')"

demo-v2: install ## Run the naive CageFlow pipeline with mocked stages
	uv run python -m flowgentic.demos.cnio.v2.main

clean: ## Remove generated run artifacts
	rm -f history.jsonl
	rm -rf reports
	find . -name "__pycache__" -type d -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
