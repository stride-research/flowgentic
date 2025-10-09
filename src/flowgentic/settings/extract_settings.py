from typing import Dict
import yaml
import os

config_path = "./config.yml"


def create_files(APP_SETTINGS: Dict):
	print(APP_SETTINGS)
	results_directory = APP_SETTINGS["agent_execution"]["results_directory"]
	os.makedirs(results_directory, exist_ok=True)


with open(config_path, "r") as f:
	APP_SETTINGS = yaml.safe_load(f)
	create_files(APP_SETTINGS)
