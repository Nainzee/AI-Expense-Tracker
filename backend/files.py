"""Upload validation (magic bytes, size, pixel limits) and PDF -> image rendering."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import List

from PIL import Image

from backend import config

Image.MAX_IMAGE_PIXELS = config.MAX_IMAGE_PIXELS


class UploadError(ValueError):
    """Raised for invalid uploads; message is safe to show to the client."""


@dataclass
class LoadedFile:
    ext: str
    pages: List[Image.Image] = field(default_factory=list)
    pdf_text: str = ""


def sniff_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"%PDF-"):
        return "pdf"
    raise UploadError("Unsupported file type. Upload a PNG, JPEG, WebP image or a PDF.")


def _render_pdf(data: bytes) -> LoadedFile:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise UploadError("Could not read the PDF file.") from exc
    try:
        if len(pdf) == 0:
            raise UploadError("The PDF has no pages.")
        pages, texts = [], []
        for i in range(min(len(pdf), config.MAX_PDF_PAGES)):
            page = pdf[i]
            pages.append(page.render(scale=200 / 72).to_pil().convert("RGB"))
            try:
                texts.append(page.get_textpage().get_text_range())
            except Exception:
                pass
        return LoadedFile(ext="pdf", pages=pages, pdf_text="\n".join(t for t in texts if t).strip())
    finally:
        pdf.close()


def load_pages(data: bytes) -> LoadedFile:
    """Validate and decode an upload into RGB page images."""
    if not data:
        raise UploadError("The uploaded file is empty.")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise UploadError("File is too large (max 15 MB).")
    ext = sniff_type(data)
    if ext == "pdf":
        return _render_pdf(data)
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > config.MAX_IMAGE_PIXELS:
            raise UploadError("Image resolution is too large (max 40 megapixels).")
        img = img.convert("RGB")
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError("Could not decode the image file.") from exc
    return LoadedFile(ext=ext, pages=[img])
