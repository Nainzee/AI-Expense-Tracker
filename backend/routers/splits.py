from datetime import date as date_cls
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator

import storage
from backend import splits
from backend.auth import require_user

router = APIRouter(tags=["Splits"], dependencies=[Depends(require_user)])


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ParticipantIn(BaseModel):
    person_id: Optional[int] = None
    value: Optional[float] = None


class ItemAssignIn(BaseModel):
    item_index: int = Field(ge=0)
    person_ids: List[Optional[int]] = Field(min_length=1)


class SplitIn(BaseModel):
    payer_id: Optional[int] = None
    method: Literal["equal", "exact", "percent", "shares", "items"] = "equal"
    participants: List[ParticipantIn] = Field(min_length=1, max_length=50)
    items: List[ItemAssignIn] = Field(default_factory=list)

    @field_validator("participants")
    @classmethod
    def _unique(cls, v: List[ParticipantIn]) -> List[ParticipantIn]:
        ids = [p.person_id for p in v]
        if len(set(ids)) != len(ids):
            raise ValueError("each person can appear only once")
        return v


class SettlementIn(BaseModel):
    person_id: int
    direction: Literal["you_paid", "they_paid"]
    amount: float = Field(gt=0)
    currency: str = Field(min_length=1, max_length=10)
    date: Optional[str] = None
    note: str = Field(default="", max_length=200)

    @field_validator("date")
    @classmethod
    def _date(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        try:
            return date_cls.fromisoformat(v).isoformat()
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD")

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return storage.to_iso_currency(v)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except splits.NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except splits.SplitError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("/people")
def list_people():
    return splits.list_people()


@router.post("/people", status_code=status.HTTP_201_CREATED)
def add_person(body: PersonIn, response: Response):
    person, created = _call(splits.add_person, body.name)
    if not created:
        response.status_code = status.HTTP_200_OK
    return person


@router.delete("/people/{person_id}")
def delete_person(person_id: int):
    try:
        splits.delete_person(person_id)
    except splits.NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except splits.SplitError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return {"deleted": person_id}


def _receipt_or_404(receipt_id: str) -> None:
    if not storage.get_receipt(receipt_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")


@router.get("/receipts/{receipt_id}/split")
def get_split(receipt_id: str):
    _receipt_or_404(receipt_id)
    s = splits.get_split(receipt_id)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This receipt is not split")
    return s


@router.put("/receipts/{receipt_id}/split")
def put_split(receipt_id: str, body: SplitIn):
    return _call(splits.save_split, receipt_id, body.model_dump())


@router.delete("/receipts/{receipt_id}/split")
def delete_split(receipt_id: str):
    _receipt_or_404(receipt_id)
    if not splits.delete_split(receipt_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This receipt is not split")
    return {"deleted": receipt_id}


@router.get("/splits")
def list_splits():
    return splits.list_splits()


@router.get("/splits/balances")
def balances():
    return splits.balances()


@router.post("/settlements", status_code=status.HTTP_201_CREATED)
def add_settlement(body: SettlementIn):
    return _call(splits.add_settlement, body.person_id, body.direction, body.amount, body.currency,
                 body.date, body.note)
