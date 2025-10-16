.PHONY: install format lint docs tests examples-sequential-research examples-sequential-financial examples-supervisor examples-supervisor-sales examples-supervisor-product-research examples-basic examples-parallel-minimal examples-parallel-llm-router
.DEFAULT_GOAL:= help


# VARIABLES
# Colors for output
RED = \033[31m
GREEN = \033[32m
YELLOW = \033[33m
BLUE = \033[34m
RESET = \033[0m

VENV_PATH =  ./.venv
VENV_ACTIVATE = source $(VENV_PATH)/bin/activate

help: ## Show this help message
	@echo "$(BLUE)Available commands:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  $(GREEN)%-15s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: #Install dependencies with pip => install graphviz 
	python3.9 -m venv $(VENV_PATH)
	$(VENV_ACTIVATE) && pip install -e ".[dev]"
	$(VENV_ACTIVATE) &&  pre-commit install
	pip install --config-settings="--global-option=build_ext" \
		--config-settings="--global-option=-I$(brew --prefix graphviz)/include/" \
		--config-settings="--global-option=-L$(brew --prefix graphviz)/lib/" \
		pygraphviz

	

format:
	$(VENV_ACTIVATE) && ruff format .

lint:
	$(VENV_ACTIVATE) && ruff check --fix .

docs:	## Renders docs locally
	$(VENV_ACTIVATE) &&  mkdocs serve
tests: ## Run tests
	$(VENV_ACTIVATE) && python -m pytest -vv -s tests/test_generator.py
examples-sequential-research:
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.design_patterns.sequential.research_agent.main
examples-sequential-financial:
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.design_patterns.sequential.financial_advisor.main
examples-supervisor-toy: ## Run supervisor toy example
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.design_patterns.supervisor.toy.main
examples-basic: ## Run toy example 
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.dummy_example 

examples-parallel-toy: ## Run parallel example with LLM-based routing
	$(VENV_ACTIVATE) && python3 examples/langgraph-integration/design_patterns/supervisor/toy/main.py

examples-supervisor-product-research: ## Run advanced supervisor pattern with product research
	$(VENV_ACTIVATE) && python3 examples/langgraph-integration/design_patterns/supervisor/product_research/main.py

