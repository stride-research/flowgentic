.PHONY: install format lint

VENV_PATH =  ./.venv
VENV_ACTIVATE = source $(VENV_PATH)/bin/activate

install: #Install dependencies with pip
	python3.9 -m venv $(VENV_PATH)
	$(VENV_ACTIVATE) && pip install -e ".[dev]"
	$(VENV_ACTIVATE) &&  pre-commit install

format:
	$(VENV_ACTIVATE) && ruff format .

lint:
	$(VENV_ACTIVATE) && ruff check --fix .