import atexit
import contextvars
import logging
import logging.handlers
import queue
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Optional

from pythonjsonlogger import jsonlogger

from .colorfulFormatter import ColoredJSONFormatter


class FileUploadFilter(logging.Filter):
	"""A custom logging filter to redact base64 file data from log records.

	This filter inspects the log message for a pattern indicating an embedded file.
	If the pattern is found, the base64 data is replaced with a placeholder
	instead of suppressing the entire log record.
	"""

	def __init__(self, pattern_to_find: str = "'type': 'file', 'file':"):
		"""Initializes the filter.

		Args:
		    pattern_to_find (str): The specific string pattern to search for in log messages
		                           to identify records that need redaction.
		"""
		super().__init__()
		# Regex to find 'file_data': 'data:...' and replace the base64 part
		self.file_data_regex = re.compile(r"('file_data':\s*')data:[^']+'")
		# Regex to find 'url': 'data:image/...' and replace the base64 part
		self.image_url_regex = re.compile(r"('url':\s*')data:image[^']+'")

	def filter(self, record: logging.LogRecord) -> bool:
		"""Checks a log record and redacts file data if present.

		The record is modified in-place to replace the base64 content with a
		placeholder.

		Args:
		    record (logging.LogRecord): The log record to be checked and modified.

		Returns:
		    bool: Always returns True, as records are modified, not suppressed.
		"""
		message = record.getMessage()
		original_message = message

		# Apply redaction for file_data pattern
		message = self.file_data_regex.sub(r"\1[...FILE DATA REDACTED...]'", message)

		# Apply redaction for image_url pattern
		message = self.image_url_regex.sub(r"\1[...IMAGE DATA REDACTED...]'", message)

		# If any redaction occurred, update the record
		if message != original_message:
			record.msg = message
			record.args = ()  # The message is now a complete string, so clear args.

			if hasattr(record, "message"):
				delattr(record, "message")

		return True  # Always allow the record to be logged


class ContextAwareQueueHandler(logging.handlers.QueueHandler):
	"""Injects dynamic fields before enqueing"""

	def prepare(self, record):
		context = LOG_CONTEXT.get()
		for key, value in context.items():
			setattr(record, key, value)
		return super().prepare(record)


class Logger:
	def __init__(
		self,
		colorful_output: bool = True,
		logger_level: str = "DEBUG",
		output_mode: Literal["stdout", "file", "both"] = "stdout",
		log_file_path: Optional[str] = None,
		max_bytes: int = 10485760,  # 10MB default
		backup_count: int = 5,
	) -> None:
		"""Initialize the Logger with configurable output modes.

		Args:
		    colorful_output: Whether to use colored output for console logs
		    logger_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
		    output_mode: Where to send logs - "stdout", "file", or "both"
		    log_file_path: Path to log file (required if output_mode is "file" or "both")
		    max_bytes: Maximum size of log file before rotation
		    backup_count: Number of backup log files to keep
		"""
		self.colorful_output = colorful_output
		self.output_mode = output_mode
		self.log_file_path = log_file_path
		self.max_bytes = max_bytes
		self.backup_count = backup_count

		# Validate configuration
		if self.output_mode not in ["stdout", "file", "both"]:
			raise ValueError(
				f"Invalid output_mode: '{self.output_mode}'. Must be one of 'stdout', 'file', 'both'."
			)
		if self.output_mode in ["file", "both"] and not self.log_file_path:
			raise ValueError(
				f"log_file_path must be provided when output_mode is '{self.output_mode}'"
			)

		self.queue_handler = self.__set_up_queue_handler()
		self.root_logger = logging.getLogger()
		self.root_logger.setLevel(self._resolve_logger_level(logger_level))
		self.root_logger.addHandler(self.queue_handler)

		atexit.register(self.shutdown)

	def _resolve_logger_level(self, logger_level: str):
		logger_level = logger_level.lower().strip()
		if logger_level == "notset":
			return logging.NOTSET
		elif logger_level == "debug":
			return logging.DEBUG
		elif logger_level == "info":
			return logging.INFO
		elif logger_level == "warning":
			return logging.WARNING
		elif logger_level == "error":
			return logging.ERROR
		elif logger_level == "critical":
			return logging.CRITICAL
		else:
			raise ValueError(f"{logger_level} is not an allowed logger level value1")

	def __set_up_queue_handler(self):
		log_queue = queue.Queue(-1)
		handlers = self.__bind_handlers()
		self.listener = logging.handlers.QueueListener(log_queue, *handlers)
		self.listener.start()

		queue_handler = ContextAwareQueueHandler(log_queue)
		return queue_handler

	def __bind_handlers(self) -> list[logging.Handler]:
		"""Create handlers based on the configured output mode.

		Returns:
		    List of configured handlers (StreamHandler and/or RotatingFileHandler)
		"""
		handlers = []
		log_filter = FileUploadFilter()

		# Add stdout handler if needed
		if self.output_mode in ["stdout", "both"]:
			stream_handler = logging.StreamHandler(stream=sys.stdout)
			formatter = self.__bind_formatter(colorful=self.colorful_output)
			stream_handler.setFormatter(formatter)
			stream_handler.addFilter(log_filter)
			handlers.append(stream_handler)

		# Add file handler if needed
		if self.output_mode in ["file", "both"]:
			# Ensure log directory exists
			log_file = Path(self.log_file_path).expanduser()
			log_file.parent.mkdir(parents=True, exist_ok=True)

			try:
				file_handler = logging.handlers.RotatingFileHandler(
					filename=str(log_file),
					maxBytes=self.max_bytes,
					backupCount=self.backup_count,
					encoding="utf-8",
				)
			except (OSError, PermissionError) as e:
				raise RuntimeError(
					f"Failed to initialize file logging at '{self.log_file_path}': {e}"
				) from e

			# File output should never be colorful
			formatter = self.__bind_formatter(colorful=False)
			file_handler.setFormatter(formatter)
			file_handler.addFilter(log_filter)
			handlers.append(file_handler)

		return handlers

	def __bind_formatter(self, colorful: bool = False):
		"""Create a formatter based on whether colorful output is desired.

		Args:
		    colorful: Whether to use colored JSON formatter

		Returns:
		    Configured formatter (ColoredJSONFormatter or JsonFormatter)
		"""
		if not colorful:
			formatter = jsonlogger.JsonFormatter(
				"%(asctime)s %(name)s %(levelname)s %(message)s",
				rename_fields={"levelname": "level", "asctime": "time"},
			)
			return formatter
		else:
			formatter = ColoredJSONFormatter(
				"%(asctime)s %(name)s %(levelname)s %(message)s",
				rename_fields={"levelname": "level", "asctime": "time"},
			)

			return formatter

	def shutdown(self):
		"""Stops the QueueListener and flushes any remaining logs."""
		if self.listener:
			logging.info("Shutting down logging listener...")
			self.listener.stop()
			logging.info("Logging listener stopped.")
		# Remove the queue handler from the root logger to prevent further logging attempts
		if self.queue_handler in self.root_logger.handlers:
			self.root_logger.removeHandler(self.queue_handler)


LOG_CONTEXT = contextvars.ContextVar("log_context", default={})


@contextmanager
def add_context_to_log(**kwargs):
	"""A context manager to add dynamic data to logs."""
	current_context = LOG_CONTEXT.get()
	new_context = {**current_context, **kwargs}

	token = LOG_CONTEXT.set(new_context)
	try:
		yield
	finally:
		LOG_CONTEXT.reset(token)
