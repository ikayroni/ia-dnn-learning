"""Utilitários para manipular PDF (recorte, normalização para Textract)."""

from __future__ import annotations

import io
import re

import fitz


def _pdf_to_bytes(doc: fitz.Document) -> bytes:
    buffer = io.BytesIO()
    doc.save(buffer, garbage=4, deflate=True)
    data = buffer.getvalue()
    doc.close()
    return data


def slice_pdf_pages(
    data: bytes,
    pagina_inicio: int | None = None,
    pagina_fim: int | None = None,
) -> tuple[bytes, int]:
    """
    Recorta PDF para intervalo 1-based inclusivo.
    Retorna (bytes do recorte, total de páginas do arquivo original).
    """
    doc = fitz.open(stream=data, filetype="pdf")
    total = len(doc)
    if total == 0:
        doc.close()
        raise ValueError("PDF sem páginas.")

    start = pagina_inicio or 1
    end = pagina_fim or total
    if start < 1 or end < start or end > total:
        doc.close()
        raise ValueError(
            f"Intervalo inválido: páginas {start}-{end} (documento tem {total} páginas)."
        )

    out = fitz.open()
    out.insert_pdf(doc, from_page=start - 1, to_page=end - 1)
    doc.close()
    return _pdf_to_bytes(out), total


def validate_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF"):
        raise ValueError(
            "Arquivo não é um PDF válido (cabeçalho ausente). "
            "Confira se o download não veio como HTML ou outro formato."
        )
    doc = fitz.open(stream=data, filetype="pdf")
    if len(doc) == 0:
        doc.close()
        raise ValueError("PDF sem páginas.")
    if doc.is_encrypted or doc.needs_pass:
        doc.close()
        raise ValueError("PDF protegido por senha. Remova a senha e envie de novo.")
    doc.close()


def normalize_pdf_for_textract(data: bytes) -> bytes:
    """Regrava PDF em formato padrão (ajuda Word/doPDF e PDFs antigos)."""
    validate_pdf(data)
    doc = fitz.open(stream=data, filetype="pdf")
    out = fitz.open()
    out.insert_pdf(doc)
    doc.close()
    return _pdf_to_bytes(out)


def rasterize_pdf_for_textract(data: bytes, dpi: int = 150) -> bytes:
    """
    Converte cada página em imagem e remonta o PDF.
    Textract aceita melhor PDFs 'só imagem' gerados assim (scans estranhos).
    """
    validate_pdf(data)
    doc = fitz.open(stream=data, filetype="pdf")
    out = fitz.open()
    for i in range(len(doc)):
        page = doc[i]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        new_page = out.new_page(width=pix.width, height=pix.height)
        new_page.insert_image(new_page.rect, pixmap=pix)
    doc.close()
    return _pdf_to_bytes(out)


def prepare_pdf_for_textract(
    data: bytes,
    *,
    force_rasterize: bool = False,
    rasterize_dpi: int = 150,
) -> tuple[bytes, str]:
    """
    Prepara bytes para upload no S3/Textract.
    Retorna (pdf_bytes, modo) onde modo é 'normalize' ou 'rasterize'.
    """
    if force_rasterize:
        return rasterize_pdf_for_textract(data, dpi=rasterize_dpi), "rasterize"
    return normalize_pdf_for_textract(data), "normalize"


def safe_pdf_filename(filename: str) -> str:
    base = filename.replace("\\", "/").split("/")[-1].strip()
    base = re.sub(r"[^\w.\-]", "_", base)
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf" if base else "documento.pdf"
    return base[:120]
