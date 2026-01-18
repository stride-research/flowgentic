from typing import Dict, List, Optional
from pydantic import BaseModel


class AgentResponse(BaseModel):
	reasoning: Optional[str] = None
	tools_results: Optional[Dict] = None
	message: Optional[str] = None
