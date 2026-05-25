from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import BASE_DIR, settings
from app.pdf_extractor import ExtractedDocument, document_to_dict, document_from_dict
from app.pdf_utils import slice_pdf_pages
from app.storage import (
    get_documento_by_hash,
    sha256_bytes,
    update_documento_ocr_done,
    upsert_documento,
)
from app.textract_ocr import run_textract_ocr_pipeline


def _jobs_dir() -> Path:
    path = BASE_DIR / settings.ocr_jobs_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    *,
    filename: str,
    pagina_inicio: Optional[int] = None,
    pagina_fim: Optional[int] = None,
    total_paginas_arquivo: Optional[int] = None,
) -> str:
    job_id = uuid.uuid4().hex
    payload = {
        "job_id": job_id,
        "status": "pending",
        "filename": filename,
        "created_at": _now(),
        "updated_at": _now(),
        "pagina_inicio": pagina_inicio,
        "pagina_fim": pagina_fim,
        "total_paginas_arquivo": total_paginas_arquivo,
        "paginas_ocr": None,
        "caracteres": None,
        "textract_job_id": None,
        "error": None,
        "document": None,
    }
    _save(payload)
    return job_id


def _save(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _job_path(data["job_id"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    path = _job_path(job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def update_job(job_id: str, **fields: Any) -> dict[str, Any]:
    data = get_job(job_id)
    if not data:
        raise KeyError(job_id)
    data.update(fields)
    _save(data)
    return data


def _run_ocr_thread(
    job_id: str,
    pdf_bytes: bytes,
    filename: str,
    documento_id: Optional[int] = None,
) -> None:
    try:
        update_job(job_id, status="processing")

        def on_status(phase: str):
            update_job(job_id, status="processing", phase=phase)

        doc, textract_job_id = run_textract_ocr_pipeline(
            pdf_bytes, filename, on_status=on_status
        )
        update_job(
            job_id,
            status="succeeded",
            phase="done",
            paginas_ocr=doc.page_count,
            caracteres=len(doc.text),
            document=document_to_dict(doc),
            error=None,
            textract_job_id=textract_job_id,
        )
        if documento_id:
            update_documento_ocr_done(
                documento_id=documento_id,
                paginas=doc.page_count,
                caracteres=len(doc.text),
                ocr_job_id=job_id,
            )
    except Exception as e:
        update_job(job_id, status="failed", phase="error", error=str(e))


def start_ocr_job(
    pdf_bytes: bytes,
    filename: str,
    *,
    pagina_inicio: Optional[int] = None,
    pagina_fim: Optional[int] = None,
) -> tuple[str, bool]:
    """Retorna (job_id, reused). reused=True se o mesmo PDF/intervalo já tinha OCR."""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_original = len(doc)
    doc.close()

    if pagina_inicio or pagina_fim:
        pdf_bytes, _ = slice_pdf_pages(pdf_bytes, pagina_inicio, pagina_fim)

    hash_id = sha256_bytes(pdf_bytes)

    existente = get_documento_by_hash(hash_id)
    if existente and existente.get("ocr_job_id"):
        prev_job_id = existente["ocr_job_id"]
        prev = get_job(prev_job_id)
        if prev and prev.get("status") == "succeeded" and prev.get("document"):
            return prev_job_id, True

    documento_id = upsert_documento(
        nome_arquivo=filename,
        hash_sha256=hash_id,
        paginas=None,
        caracteres=None,
        ocr_job_id=None,
        fonte="ocr",
    )

    job_id = create_job(
        filename=filename,
        pagina_inicio=pagina_inicio,
        pagina_fim=pagina_fim,
        total_paginas_arquivo=total_original,
    )

    thread = threading.Thread(
        target=_run_ocr_thread,
        args=(job_id, pdf_bytes, filename, documento_id),
        daemon=True,
    )
    thread.start()
    return job_id, False


def load_document_from_job(job_id: str) -> ExtractedDocument:
    data = get_job(job_id)
    if not data:
        raise KeyError(f"Job não encontrado: {job_id}")
    if data["status"] != "succeeded" or not data.get("document"):
        raise ValueError(
            f"Job {job_id} não está pronto (status={data.get('status')}). "
            "Aguarde GET /ocr/jobs/{id} retornar succeeded."
        )
    return document_from_dict(data["document"])
