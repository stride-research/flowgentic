import logging

from ._logging import configure_logging

__all__ = ["configure_logging"]

logging.getLogger(__name__).addHandler(logging.NullHandler())
