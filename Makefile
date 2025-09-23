.PHONY: install format lint docs

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
	mkdocs serve