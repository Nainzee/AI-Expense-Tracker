import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend import config
from backend.auth import require_user
from backend.statements import service
from backend.statements.parsing import StatementError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/statements", tags=["Statements"], dependencies=[Depends(require_user)])


class ColumnMapping(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    header_row: int = Field(ge=-1)
    date: int = Field(ge=0)
    description: int = Field(ge=0)
    debit: Optional[int] = Field(default=None, ge=0)
    credit: Optional[int] = Field(default=None, ge=0)
    amount: Optional[int] = Field(default=None, ge=0)
    drcr: Optional[int] = Field(default=None, ge=0)
    balance: Optional[int] = Field(default=None, ge=0)
    reference: Optional[int] = Field(default=None, ge=0)
    date_order: Literal["dmy", "mdy", "ymd"] = "dmy"

    @model_validator(mode="after")
    def _check(self):
        if self.debit is None and self.credit is None and self.amount is None:
            raise ValueError("choose a debit/credit or amount column")
        used = [v for v in (self.date, self.description, self.debit, self.credit, self.amount, self.drcr,
                            self.balance, self.reference) if v is not None]
        if len(used) != len(set(used)):
            raise ValueError("each column can be used only once")
        return self


class ReparseIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    token: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping: ColumnMapping


class CommitIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    token: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping: Optional[ColumnMapping] = None
    account_label: Optional[str] = Field(default=None, max_length=60)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except StatementError as exc:
        detail = {"message": str(exc), "code": exc.code} if exc.code else str(exc)
        raise HTTPException(exc.status, detail)


@router.post("/preview")
def preview(file: UploadFile = File(...), password: Optional[str] = Form(default=None, max_length=128),
            account_label: str = Form(default="", max_length=60)):
    data = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File is too large (max 15 MB).")
    try:
        return _call(service.preview, data, file.filename or "statement", password, account_label.strip())
    except HTTPException:
        raise
    except Exception:
        log.exception("Statement preview failed")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not read this statement.")


@router.post("/reparse")
def reparse(body: ReparseIn):
    return _call(service.reparse, body.token, body.mapping.model_dump())


@router.post("/commit")
def commit(body: CommitIn):
    return _call(service.commit, body.token, body.mapping.model_dump() if body.mapping else None,
                 body.account_label.strip() if body.account_label is not None else None)


@router.get("")
def list_statements():
    return service.list_statements()


@router.delete("/{statement_id}")
def delete_statement(statement_id: int):
    if not service.delete_statement(statement_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement not found")
    return {"deleted": statement_id}
