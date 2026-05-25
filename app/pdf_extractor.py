from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Optional

import fitz  # PyMuPDF


@dataclass
class ExtractedDocument:
    text: str
    page_count: int
    page_markers: list[tuple[int, int]]  # (offset_char, page_num)


def document_to_dict(doc: ExtractedDocument) -> dict[str, Any]:
    return {
        "text": doc.text,
        "page_count": doc.page_count,
        "page_markers": doc.page_markers,
    }


def document_from_dict(data: dict[str, Any]) -> ExtractedDocument:
    return ExtractedDocument(
        text=data["text"],
        page_count=data["page_count"],
        page_markers=[tuple(m) for m in data["page_markers"]],
    )


def _extract_native(doc: fitz.Document) -> ExtractedDocument:
    parts: list[str] = []
    markers: list[tuple[int, int]] = []
    offset = 0
    pages_with_text = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_text = page.get_text("text").strip()
        markers.append((offset, page_num + 1))
        if page_text:
            parts.append(page_text)
            offset += len(page_text) + 2
            if len(page_text) >= 30:
                pages_with_text += 1

    full_text = "\n\n".join(parts)
    return ExtractedDocument(
        text=full_text,
        page_count=len(doc),
        page_markers=markers,
    ), pages_with_text


def pdf_has_native_text(data: bytes, min_chars_per_page: Optional[int] = None) -> bool:
    """Amostra: PDF tem texto selecionável suficiente sem OCR?"""
    from app.config import settings

    threshold = min_chars_per_page or settings.min_chars_per_page_native
    doc = fitz.open(stream=data, filetype="pdf")
    if len(doc) == 0:
        doc.close()
        return False

    sample_indices = [0, len(doc) // 2, len(doc) - 1] if len(doc) > 2 else list(range(len(doc)))
    ok = 0
    for i in sample_indices:
        text = doc[i].get_text("text").strip()
        if len(text) >= threshold:
            ok += 1
    doc.close()
    return ok >= max(1, len(sample_indices) // 2)


def extract_text_from_pdf(
    data: bytes,
    *,
    allow_empty: bool = False,
) -> ExtractedDocument:
    doc = fitz.open(stream=data, filetype="pdf")
    extracted, pages_with_text = _extract_native(doc)
    doc.close()

    if not extracted.text.strip():
        if allow_empty:
            return extracted
        raise ValueError(
            "Não foi possível extrair texto do PDF. "
            "Use POST /ocr/pdf para OCR com Amazon Textract (PDF escaneado)."
        )

    if pages_with_text == 0 and len(extracted.text) < 100:
        raise ValueError(
            "Texto insuficiente no PDF (provável scan). "
            "Use POST /ocr/pdf para processar com Textract."
        )

    return extracted


def extract_text_from_pdf_file(path: str) -> ExtractedDocument:
    with open(path, "rb") as f:
        return extract_text_from_pdf(f.read())


def extract_text_from_pdf_stream(file: io.BufferedIOBase) -> ExtractedDocument:
    return extract_text_from_pdf(file.read())
