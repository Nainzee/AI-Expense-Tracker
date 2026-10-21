import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from backend import config, db, extraction
from backend.auth import require_user
from backend.files import UploadError

log = logging.getLogger(__name__)
router = APIRouter(tags=["Scan"], dependencies=[Depends(require_user)])


@router.post("/scan")
def scan(file: UploadFile = File(..., description="Receipt image (PNG/JPEG/WebP) or PDF")):
    data = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    try:
        result = extraction.extract(data)
    except UploadError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception:
        log.exception("Scan failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not process the receipt.")

    upload_id = db.save_upload(data, result.pop("file_ext"))
    ext = result["extraction"]
    result["upload_id"] = upload_id
    result["duplicate_of"] = db.find_duplicate(upload_id.split(".")[0], ext["merchant_name"], ext["date"] or "",
                                               ext["total"])
    return result
