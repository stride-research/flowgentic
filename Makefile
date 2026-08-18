.PHONY: install install-dev format lint docs tests tests-units tests-integration-llm examples-sequential-research examples-sequential-financial examples-supervisor examples-supervisor-sales examples-supervisor-product-research examples-basic examples-parallel-minimal examples-parallel-llm-router examples-mcp-sales-analytics
.DEFAULT_GOAL:= help


# VARIABLES
RED = \033[31m
GREEN = \033[32m
YELLOW = \033[33m
BLUE = \033[34m
RESET = \033[0m

UV ?= uv
UV_RUN = $(UV) run
UV_RUN_DEV = $(UV) run --extra dev

help: 
	@echo "$(BLUE)Available commands:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  $(GREEN)%-15s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ============
# =  SET-UP  =
# ============

install: ## Create or update the locked runtime environment
	$(UV) sync --frozen

install-dev: ## Create or update the locked development environment
	$(UV) sync --frozen --extra dev
	$(UV_RUN_DEV) pre-commit install


# ============
# =   CI/CD  =
# ============
format:
	$(UV_RUN_DEV) ruff format .

lint:
	$(UV_RUN_DEV) ruff check .
docs:	## Renders docs locally
	$(UV_RUN_DEV) mkdocs serve
tests: ## Run tests
	$(UV_RUN_DEV) pytest -vv \
                  --cov=flowgentic \
                  --cov-report=html:coverage_report \
                  --cov-report=term-missing \
                  --cov-report=term:skip-covered \
                  tests/
tests-units: ## Run unit tests
	$(UV_RUN_DEV) pytest -vv \
				--cov=flowgentic \
				--cov-report=html:coverage_report \
				--cov-report=term-missing \
				--cov-report=term:skip-covered \
				tests/unit

tests-integration-llm: ## Run opt-in examples that require model credentials
	FLOWGENTIC_RUN_LLM_INTEGRATION=1 $(UV_RUN_DEV) pytest -vv tests/integration

# ============
# = EXAMPLES =
# ============

## LangGraph
### Design Patterns 
examples-chatbot-toy: 
	$(UV_RUN) python -m examples.langgraph-integration.design_patterns.chatbot.toy
examples-sequential-research:
	$(UV_RUN) python -m examples.langgraph-integration.design_patterns.sequential.research_agent.main
examples-supervisor-toy: 
	$(UV_RUN) python -m examples.langgraph-integration.design_patterns.supervisor.toy.main
#### Memory
examples-sequential-research-memory:
	$(UV_RUN) python -m examples.langgraph-integration.design_patterns.sequential.research_agent_memory.main
### Services
examples-services-intermittent-task: 
	$(UV_RUN) python -m examples.langgraph-integration.service-task.service-intermittent
### Miscellaneous
examples-runtime-graph:
	$(UV_RUN) python -m examples.langgraph-integration.miscellaneous.runtime-graph-creation
