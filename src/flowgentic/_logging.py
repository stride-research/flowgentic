"""Opt-in console output for Flowgentic's log records.

Flowgentic follows the standard library's advice for libraries: its modules log
through ``logging.getLogger(__name__)`` and the ``flowgentic`` logger has no
handler, so without configuration Python's last-resort handler prints warnings
and errors to stderr. Handlers and levels are the application's to configure.
"""

import logging
from typing import TextIO

_HANDLER_NAME = "flowgentic"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(
	level: int | str = logging.INFO, *, stream: TextIO | None = None
) -> None:
	"""Print Flowgentic's log records, for scripts and debugging.

	Attaches one handler to the ``flowgentic`` logger, never to the root logger,
	and sets that logger's level. Calling it again replaces the handler. Records
	still propagate, so handlers the application configures see them too.

	Args:
	    level: The ``flowgentic`` logger's level, as ``logging`` accepts it:
	        ``logging.DEBUG`` or ``"DEBUG"``.
	    stream: Where to write the records; ``sys.stderr`` when omitted.

	Example:
	    >>> import flowgentic
	    >>> flowgentic.configure_logging("DEBUG")
	"""
	logger = logging.getLogger("flowgentic")
	logger.setLevel(level)
	for handler in [h for h in logger.handlers if h.name == _HANDLER_NAME]:
		logger.removeHandler(handler)
		handler.close()
	handler = logging.StreamHandler(stream)
	handler.set_name(_HANDLER_NAME)
	handler.setFormatter(logging.Formatter(_FORMAT))
	logger.addHandler(handler)
