import logging
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend import insights
from backend.auth import require_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/insights", tags=["Insights"], dependencies=[Depends(require_user)])


class ChatTurn(BaseModel):
    role: str
    content: str = Field(max_length=8000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: List[ChatTurn] = Field(default_factory=list, max_length=50)
    currency: Optional[str] = None


@router.get("")
def get_insights(month: Optional[str] = None, currency: Optional[str] = None, refresh: bool = False):
    if month and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "month must be YYYY-MM")
    return insights.get_insights(month, currency, refresh)


@router.post("/chat")
def chat(body: ChatIn):
    try:
        return insights.chat(body.message, [t.model_dump() for t in body.history], body.currency)
    except Exception:
        log.exception("Chat failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "The assistant could not answer right now.")
