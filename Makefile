.PHONY: install format lint docs tests examples-sequential-research examples-sequential-financial examples-supervisor examples-supervisor-sales examples-supervisor-product-research examples-basic examples-parallel-minimal examples-parallel-llm-router examples-mcp-sales-analytics
.DEFAULT_GOAL:= help


# VARIABLES
RED = \033[31m
GREEN = \033[32m
YELLOW = \033[33m
BLUE = \033[34m
RESET = \033[0m

VENV_PATH =  ./.venv
VENV_ACTIVATE = source $(VENV_PATH)/bin/activate

help: 
	@echo "$(BLUE)Available commands:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  $(GREEN)%-15s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ============
# =  SET-UP  =
# ============

install: #Install dependencies with pip => install graphviz 
	$(VENV_ACTIVATE) && pip install -e "."
	$(VENV_ACTIVATE) &&  pre-commit install
	$(VENV_ACTIVATE) && pip install --config-settings="--global-option=build_ext" \
		--config-settings="--global-option=-I$$(brew --prefix graphviz)/include/" \
		--config-settings="--global-option=-L$$(brew --prefix graphviz)/lib/" \
		pygraphviz
install-dev: 
	$(VENV_ACTIVATE) && pip install -e ".[dev]"
	$(VENV_ACTIVATE) &&  pre-commit install
	$(VENV_ACTIVATE) && pip install --config-settings="--global-option=build_ext" \
		--config-settings="--global-option=-I$$(brew --prefix graphviz)/include/" \
		--config-settings="--global-option=-L$$(brew --prefix graphviz)/lib/" \
		pygraphviz


# ============
# =   CI/CD  =
# ============
format:
	$(VENV_ACTIVATE) && ruff format .

lint:
	$(VENV_ACTIVATE) && ruff check --fix .
docs:	## Renders docs locally
	$(VENV_ACTIVATE) &&  mkdocs serve
tests: ## Run tests
	$(VENV_ACTIVATE) && pytest -vv -s \
                  --cov=flowgentic \
                  --cov-report=html:coverage_report \
                  --cov-report=term-missing \
                  --cov-report=term:skip-covered \
                  tests/
tests-units: 
	$(VENV_ACTIVATE) && pytest -vv -s \
				--cov=flowgentic \
				--cov-report=html:coverage_report \
				--cov-report=term-missing \
				--cov-report=term:skip-covered \
				tests/unit

# ============
# = EXAMPLES =
# ============

## LangGraph
### Design Patterns 
examples-chatbot-toy: 
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.design_patterns.chatbot.toy
examples-sequential-research:
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.design_patterns.sequential.research_agent.main
examples-supervisor-toy: 
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.design_patterns.supervisor.toy.main
#### Memory
examples-sequential-research-memory:
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.design_patterns.sequential.research_agent_memory.main
### Services
examples-services-intermittent-task: 
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.service-task.service-intermittent
### Miscellaneous
examples-runtime-graph:
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.miscellaneous.runtime-graph-creation
