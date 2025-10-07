.PHONY: install format lint docs tests examples-sequential-research examples-sequential-financial examples-basic

VENV_PATH =  ./.venv
VENV_ACTIVATE = source $(VENV_PATH)/bin/activate

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

docs:	
	$(VENV_ACTIVATE) &&  mkdocs serve
tests:
	$(VENV_ACTIVATE) && python -m pytest -vv -s tests/test_introspection.py
examples-sequential-research:
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.design_patterns.sequential.research_agent.main
examples-sequential-financial:
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.design_patterns.sequential.financial_advisor.main
examples-basic:
	$(VENV_ACTIVATE) && python3 -m examples.langgraph-integration.dummy_example 
