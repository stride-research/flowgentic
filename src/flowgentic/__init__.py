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
	logger_kwargs.update(
		{
			"log_file_path": file_config.get("path", "logs/flowgentic.log"),
			"max_bytes": file_config.get("max_bytes", 10485760),
			"backup_count": file_config.get("backup_count", 5),
		}
	)

logger_instance = Logger(**logger_kwargs)  # Initiating logger
