from .utils import Logger

from .settings.extract_settings import APP_SETTINGS

logger_level = APP_SETTINGS["logger"]["level"]
logger_instance = Logger(
	colorful_output=True, logger_level=logger_level
)  # Initiating logger
