.PHONY: install format lint docs tests examples-sequential-research examples-sequential-financial examples-supervisor examples-supervisor-sales examples-supervisor-product-research examples-basic examples-parallel-minimal examples-parallel-llm-router examples-mcp-sales-analytics

.DEFAULT_GOAL:= help
SHELL := /bin/bash


# VARIABLES FOR PRETTY PRINT
RED = \033[31m
GREEN = \033[32m
YELLOW = \033[33m
BLUE = \033[34m
RESET = \033[0m

# VARIABLES, GENERAL
VENV_PATH =  ./.venv
VENV_ACTIVATE = source $(VENV_PATH)/bin/activate


# 1) Set-up
help: ## Show this help message
	@printf "$(BLUE) Available commands: $(RESET)\n"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  $(GREEN)%-15s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
install: ## Installation
	uv venv $(VENV_PATH) --python 3.10
	uv pip install -e ".[dev]"

# 2) Examples
examples-lg-asyncflow:  ## Langgraph + Asyncflow
	$(VENV_ACTIVATE) && python3 -m examples.langgraph_asyncflow
examples-ca-asyncflow:  ## CrewAI + Asyncfow
	$(VENV_ACTIVATE) && python3 -m examples.crewai_asyncflow
examples-ag-asyncflow:  ## AutoGen + Asyncfow
	$(VENV_ACTIVATE) && python3 -m examples.autogen_asyncflow
examples-lg-parsl:  ## AutoGen + Parsl
	$(VENV_ACTIVATE) && python3 -m examples.langgraph_parsl
examples-ag-parsl:  ## AutoGen + Parsl
	$(VENV_ACTIVATE) && python3 -m examples.autogen_parsl

# 3) Benchmark
benchmark: ## Run the experiments in the benchmarking
	$(VENV_ACTIVATE) && python3 -m tests.benchmark.data_generation.run_experiments

benchmark-suite: ## Run the full benchmark suite with multiple configs
	$(VENV_ACTIVATE) && python3 -m tests.benchmark.run_benchmark_suite
