from typing import List, Dict, Any


class AgentState:
	"""Builder design pattern"""

	def __init__(self):
		self.system_instructions: str = ""
		self.user_input: str = ""
		self.reasoning: str = ""
		self.message: str = ""
		self.tool_results: List[Dict[str, Any]] = []
		self.metadata: Dict[str, Any] = {}

	def add_system_instructions(self, instructions: str) -> "AgentState":
		self.system_instructions = instructions
		return self

	def add_user_input(self, user_input: str) -> "AgentState":
		self.user_input = user_input
		return self

	def add_reasoning(self, reasoning: str) -> "AgentState":
		self.reasoning = reasoning
		return self

	def add_message(self, message: str) -> "AgentState":
		self.message = message
		return self

	def add_tool_results(self, results: Dict[str, Any]) -> "AgentState":
		self.tool_results.append(results)
		return self

	def add_metadata(self, key: str, value: Any) -> "AgentState":
		self.metadata[key] = value
		return self

	def reset(self) -> "AgentState":
		self.system_instructions = ""
		self.user_input = ""
		self.reasoning = ""
		self.message = ""
		self.tool_results = []
		self.metadata = {}
		return self
