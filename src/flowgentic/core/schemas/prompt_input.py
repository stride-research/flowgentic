from typing import Optional, List
from pydantic import BaseModel


class PromptInput(BaseModel):
	user_input: str
	system_input: str
	conversation_history: Optional[List[dict]] = (
		None  # [{"role": "user", "content": "..."}, ...]
	)
	metadata: Optional[str] = None
