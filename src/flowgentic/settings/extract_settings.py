from typing import Dict
import yaml
import os
from pathlib import Path

# Default settings
DEFAULT_SETTINGS = {
	"agent_execution": {
		"results_directory": "agent_execution_results",
		"execution_summary_path": "agent_execution_results/execution_summary.md",
		"graph_image_path": "agent_execution_results/agent_graph.png",
	},
	"logger": {
		"level": "DEBUG"
	}
}

# Try to find config.yml in multiple locations
def load_config() -> Dict:
	"""Load configuration from config.yml or use defaults."""
	# Location 1: Current working directory
	cwd_config = Path.cwd() / "config.yml"
	if cwd_config.exists():
		with open(cwd_config, "r") as f:
			return yaml.safe_load(f)
	
	# Location 2: User's home directory
	home_config = Path.home() / ".flowgentic" / "config.yml"
	if home_config.exists():
		with open(home_config, "r") as f:
			return yaml.safe_load(f)
	
	# Location 3: Environment variable
	config_env = os.getenv("FLOWGENTIC_CONFIG")
	if config_env:
		config_path = Path(config_env)
		if config_path.exists():
			with open(config_path, "r") as f:
				return yaml.safe_load(f)
	
	# Location 4: Package directory (for development)
	current_file = Path(__file__).resolve()
	project_root = current_file.parent.parent.parent.parent
	dev_config = project_root / "config.yml"
	if dev_config.exists():
		with open(dev_config, "r") as f:
			return yaml.safe_load(f)
	
	# Fall back to defaults
	return DEFAULT_SETTINGS

APP_SETTINGS = load_config()
