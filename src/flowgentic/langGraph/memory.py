raise NotImplementedError()


from abc import ABC, abstractmethod
from typing import List

from langchain_core.messages import BaseMessage, SystemMessage


class MemoryMechanism(ABC):
	def __init__(self, max_messages: int = 20) -> None:
		self._max_messages = max_messages

	@abstractmethod
	def reduce_message_list(self, messages: List[BaseMessage]):
		pass


class MemoryTrimmer(MemoryMechanism):
	def __init__(self, max_messages: int = 20) -> None:
		super().__init__(max_messages)

	def reduce_message_list(self, messages: List[BaseMessage]):
		if len(messages) < self._max_messages:
			return messages

		# Always keep system messages:
		system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
		other_msgs = [m for m in messages if not isinstance(m, SystemMessage)]

		trimmed_msgs = other_msgs[-self._max_messages :]
		return system_msgs + trimmed_msgs
