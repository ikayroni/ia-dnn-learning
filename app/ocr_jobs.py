from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class _Cancelled(RuntimeError):
    """Cancelamento solicitado pelo usuário."""


_CANCEL_FLAGS: set[str] = set()
_CANCEL_LOCK = threading.Lock()


_ACTIVE_THREADS: dict[str, threading.Thread] = {}
_ACTIVE_LOCK = threading.Lock()


def _register_thread(job_id: str, thread: threading.Thread) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_THREADS[job_id] = thread


def _unregister_thread(job_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_THREADS.pop(job_id, None)


def has_active_thread(job_id: str) -> bool:
    with _ACTIVE_LOCK:
        t = _ACTIVE_THREADS.get(job_id)
    return bool(t and t.is_alive())


def request_cancel(job_id: str) -> bool:
    """Sinaliza cancelamento e marca status como 'cancelled' no JSON imediatamente.

    Retorna True se havia thread viva (cancelamento "soft" — vai parar no próximo poll);
    False se o job era órfão (sem thread) — marca direto como cancelled.
    """
    with _CANCEL_LOCK:
        _CANCEL_FLAGS.add(job_id)
    alive = has_active_thread(job_id)
    msg = (
        "Cancelado pelo usuário. O Textract pode continuar processando na AWS "
        "(a cobrança das páginas já enviadas se aplica)."
        if alive
        else "Job órfão (sem thread ativa) — marcado como cancelado. "
        "Provavelmente o servidor reiniciou durante o processamento."
    )
    try:
        update_job(job_id, status="cancelled", phase="cancelled", error=msg)
    except KeyError:
        pass
    return alive


def is_cancelled(job_id: str) -> bool:
    with _CANCEL_LOCK:
        return job_id in _CANCEL_FLAGS


def _clear_cancel(job_id: str) -> None:
    with _CANCEL_LOCK:
        _CANCEL_FLAGS.discard(job_id)


def cleanup_orphan_jobs() -> int:
    """Marca como 'interrupted' jobs que ficaram 'processing' sem thread viva
    (acontece quando o servidor reinicia durante OCR). Chamado no startup.

    Retorna quantos jobs foram limpos.
    """
    cleaned = 0
    for path in _jobs_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("status") in ("pending", "processing"):
            if has_active_thread(data.get("job_id", "")):
                continue
            data["status"] = "interrupted"
            data["phase"] = "interrupted"
            data["error"] = (
                "Interrompido: o servidor reiniciou durante o processamento "
                "(thread perdida). O Textract pode ter continuado na AWS; "
                "use o histórico ou reenvie o PDF para um novo OCR."
            )
            data["finished_at"] = _now()
            _save(data)
            cleaned += 1
    return cleaned

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
    import time as _time

    t_start = _time.time()
    try:
        update_job(job_id, status="processing", started_at=_now())

        def check_cancel():
            return is_cancelled(job_id)

        def on_status(phase: str):
            if check_cancel():
                raise _Cancelled("cancelado pelo usuário")
            update_job(
                job_id,
                status="processing",
                phase=phase,
                elapsed_seconds=int(_time.time() - t_start),
            )

        def on_poll(polls: int, elapsed: int, textract_status: str):
            if check_cancel():
                raise _Cancelled("cancelado pelo usuário")
            update_job(
                job_id,
                textract_polls=polls,
                textract_status=textract_status,
                textract_elapsed_seconds=elapsed,
                elapsed_seconds=int(_time.time() - t_start),
            )

        doc, textract_job_id = run_textract_ocr_pipeline(
            pdf_bytes, filename, on_status=on_status, on_poll=on_poll
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
            elapsed_seconds=int(_time.time() - t_start),
            finished_at=_now(),
        )
        if documento_id:
            update_documento_ocr_done(
                documento_id=documento_id,
                paginas=doc.page_count,
                caracteres=len(doc.text),
                ocr_job_id=job_id,
            )
    except _Cancelled:
        update_job(
            job_id,
            status="cancelled",
            phase="cancelled",
            error="Cancelado pelo usuário. O Textract da AWS pode continuar processando "
                  "do lado do servidor (a AWS não expõe API de cancelamento); a cobrança "
                  "das páginas já enviadas se aplica.",
            elapsed_seconds=int(_time.time() - t_start),
            finished_at=_now(),
        )
    except Exception as e:
        if is_cancelled(job_id):
            update_job(
                job_id,
                status="cancelled",
                phase="cancelled",
                error=f"Cancelado durante: {e}",
                elapsed_seconds=int(_time.time() - t_start),
                finished_at=_now(),
            )
        else:
            update_job(
                job_id,
                status="failed",
                phase="error",
                error=str(e),
                elapsed_seconds=int(_time.time() - t_start),
                finished_at=_now(),
            )
    finally:
        _clear_cancel(job_id)
        _unregister_thread(job_id)


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
    _register_thread(job_id, thread)
    thread.start()
    return job_id, False


def list_jobs(only_active: bool = False) -> list[dict[str, Any]]:
    """Lista jobs OCR existentes (pasta data/ocr_jobs/*.json), ordenados do mais recente."""
    jobs: list[dict[str, Any]] = []
    for path in _jobs_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if only_active and data.get("status") not in ("pending", "processing"):
            continue
        data.pop("document", None)  # nao trafegar o texto cru
        jobs.append(data)
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs


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
