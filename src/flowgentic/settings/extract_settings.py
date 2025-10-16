from typing import Dict
import yaml
import os
from pathlib import Path

# Find config.yml in project root
current_file = Path(__file__).resolve()
project_root = current_file.parent.parent.parent.parent
config_path = project_root / "config.yml"


def create_files(APP_SETTINGS: Dict):
	print(APP_SETTINGS)


with open(config_path, "r") as f:
	APP_SETTINGS = yaml.safe_load(f)
	create_files(APP_SETTINGS)
