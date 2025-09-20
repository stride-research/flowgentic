import atexit
import contextvars
import logging
import logging.handlers
import queue
import re
import sys
from contextlib import contextmanager

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
	def __init__(self, colorful_output=True) -> None:
		self.colorful_output = colorful_output
		self.queue_handler = self.__set_up_queue_handler()
		self.root_logger = logging.getLogger()
		self.root_logger.setLevel(logging.DEBUG)
		self.root_logger.addHandler(self.queue_handler)

		atexit.register(self.shutdown)

	def __set_up_queue_handler(self):
		log_queue = queue.Queue(-1)
		console_handler = self.__bind_handlers()
		self.listener = logging.handlers.QueueListener(log_queue, console_handler)
		self.listener.start()

		queue_handler = ContextAwareQueueHandler(log_queue)
		return queue_handler

	def __bind_handlers(self) -> logging.Handler:
		stream_handler = logging.StreamHandler(stream=sys.stdout)
		formatter = self.__bind_formatter()
		stream_handler.setFormatter(formatter)
		log_filter = FileUploadFilter()
		stream_handler.addFilter(log_filter)
		return stream_handler

	def __bind_formatter(self):
		if not self.colorful_output:
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
