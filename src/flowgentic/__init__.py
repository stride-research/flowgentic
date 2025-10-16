from .utils import ChatLLMProvider
try:
    from .utils.observability.compat import Logger
except ImportError:
    # Fallback to original Logger if OBSERVE not available
    from .utils import Logger

logger_instance = Logger(colorful_output=True)  # Initiating logger (now uses OBSERVE)
