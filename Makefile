.PHONY: install-pip

VENV_PATH =  ./.venv
VENV_ACTIVATE = source $(VENV_PATH)/bin/activate

install-pip: #Install dependencies with pip
	python3.9 -m venv $(VENV_PATH)
	$(VENV_ACTIVATE) && pip install -e ".[dev]"