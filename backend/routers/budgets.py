from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from backend import db
from backend.auth import require_user
from categorizer import CANONICAL_CATEGORIES

router = APIRouter(prefix="/budgets", tags=["Budgets"], dependencies=[Depends(require_user)])


class BudgetIn(BaseModel):
    category: str
    monthly_limit: float = Field(gt=0)
    currency: str = "INR"

    @field_validator("category")
    @classmethod
    def _cat(cls, v: str) -> str:
        if v not in CANONICAL_CATEGORIES:
            raise ValueError(f"category must be one of {CANONICAL_CATEGORIES}")
        return v


@router.get("")
def list_budgets():
    return db.list_budgets()


@router.put("")
def put_budgets(body: List[BudgetIn]):
    db.upsert_budgets([b.model_dump() for b in body])
    return db.list_budgets()


@router.delete("/{category}")
def delete_budget(category: str):
    if not db.delete_budget(category):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Budget not found")
    return {"deleted": category}
