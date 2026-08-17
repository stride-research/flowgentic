from typing import List, Optional
from pydantic import BaseModel


class ProvidersResponse(BaseModel):
	reasoning: Optional[str] = None
	tools_to_use: Optional[List] = []
	message: Optional[str] = None
