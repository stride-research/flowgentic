from .utils import Logger

from .settings.extract_settings import APP_SETTINGS

logger_config = APP_SETTINGS["logger"]
logger_level = logger_config["level"]
output_mode = logger_config.get("output", "stdout")

# Prepare logger kwargs
logger_kwargs = {
	"colorful_output": True,
	"logger_level": logger_level,
	"output_mode": output_mode,
}

# Add file configuration if needed
if output_mode in ["file", "both"]:
	file_config = logger_config.get("file") or {}
	# Only pass file config if provided; Logger class will use its own defaults
	if "path" in file_config:
		logger_kwargs["log_file_path"] = file_config["path"]
	if "max_bytes" in file_config:
		logger_kwargs["max_bytes"] = file_config["max_bytes"]
	if "backup_count" in file_config:
		logger_kwargs["backup_count"] = file_config["backup_count"]

logger_instance = Logger(**logger_kwargs)  # Initiating logger
