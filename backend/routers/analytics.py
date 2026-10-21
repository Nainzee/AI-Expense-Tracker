from datetime import date as date_cls
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend import analytics
from backend.auth import require_user

router = APIRouter(prefix="/analytics", tags=["Analytics"], dependencies=[Depends(require_user)])


def _check_date(v: Optional[str], name: str) -> Optional[str]:
    if not v:
        return None
    try:
        return date_cls.fromisoformat(v).isoformat()
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"'{name}' must be YYYY-MM-DD")


@router.get("/summary")
def summary(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
            currency: Optional[str] = None):
    return analytics.summary(_check_date(frm, "from"), _check_date(to, "to"), currency)
