from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ModelTestRequest(BaseModel):
    message: str = "Hello"


class ModelTestResponse(BaseModel):
    component_id: str
    model_id: Optional[str] = None
    provider: Optional[str] = None
    success: bool
    response: Optional[str] = None
    error: Optional[str] = None
