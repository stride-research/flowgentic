class MemoryManger:
	def __init__(self) -> None:
		pass

	def get_context(self):
		pass

	def set_context(self):
		pass

	def record_state(self):
		pass


class NullMemoryManager(MemoryManger):
	"""A no-op memory manager for stateless agents."""

	def __init__(self) -> None:
		pass

	def get_context(self):
		pass

	def set_context(self):
		pass

	def record_state(self, state):
		return ""  # Returns empty context
