import inspect
from typing import get_type_hints, Callable

# Map Python types to JSON Schema types
_TYPE_MAP = {
	str: "string",
	int: "integer",
	float: "number",
	bool: "boolean",
	list: "array",
	dict: "object",
}


class Tool:
	def __init__(self, func: Callable, **config):
		self.func = func
		self.name = func.__name__
		self.description = func.__doc__ or ""
		self.config = config

	def get_schema(self) -> dict:
		"""Returns OpenAI-compatible tool schema."""
		hints = get_type_hints(self.func)
		sig = inspect.signature(self.func)

		properties = {}
		required = []

		for param_name, param in sig.parameters.items():
			param_type = hints.get(param_name, str)
			properties[param_name] = {"type": _TYPE_MAP.get(param_type, "string")}
			if param.default is inspect.Parameter.empty:
				required.append(param_name)

		return {
			"type": "function",
			"function": {
				"name": self.name,
				"description": self.description.strip(),
				"parameters": {
					"type": "object",
					"properties": properties,
					"required": required,
				},
			},
		}

	async def execute(self, **kwargs):
		return await self.func(**kwargs)
