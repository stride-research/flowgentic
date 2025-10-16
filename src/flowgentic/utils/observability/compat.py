"""
Compatibility layer for existing Flowgentice Logger API.

Provides drop-in replacement that uses OBSERVE under the hood.
"""

import logging
from contextlib import contextmanager
from typing import Optional

from observe import create_logger


class FlowgenticeLogger:
	"""
	Backward-compatible Logger class using OBSERVE.

	Drop-in replacement for existing flowgentic.utils.Logger
	"""

	def __init__(self, colorful_output: bool = True):
		self.colorful_output = colorful_output
		self._observe_logger: Optional[logging.Logger] = None

	def _ensure_logger(self):
		"""Lazy initialization of OBSERVE logger"""
		if self._observe_logger is None:
			try:
				self._observe_logger = create_logger("flowgentice")
			except RuntimeError:
				# If OBSERVE not initialized, fall back to standard logging
				self._observe_logger = logging.getLogger("flowgentice")

	def get_logger(self, name: str):
		"""Get a standard library logger (for compatibility)"""
		return logging.getLogger(name)

	def shutdown(self):
		"""Shutdown observability (handled by OBSERVE)"""
		# OBSERVE handles cleanup automatically
		pass


# Context manager for backward compatibility
@contextmanager
def add_context_to_log(**kwargs):
	"""
	Context manager for adding context to logs.

	Compatible with existing LOG_CONTEXT usage.
	"""
	# OBSERVE handles context automatically via correlation IDs
	# This is a no-op for compatibility
	yield


# Global instance for backward compatibility
Logger = FlowgenticeLogger
