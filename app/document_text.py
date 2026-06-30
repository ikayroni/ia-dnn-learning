"""Carrega texto de documentos: OCR, cache em disco, jobs órfãos ou texto inline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.config import BASE_DIR
from app.ocr_jobs import get_job, list_jobs, load_document_from_job
from app.pdf_extractor import ExtractedDocument, document_from_dict, document_to_dict
from app.storage import get_documento_row, update_documento_ocr_done


CACHE_DIR = BASE_DIR / "data" / "documentos"


def save_document_text_cache(
    doc: ExtractedDocument,
    *,
    hash_sha256: Optional[str] = None,
    documento_id: Optional[int] = None,
) -> None:
    """Persiste texto extraído para reuso (trilha, regerar questões, etc.)."""
    if not hash_sha256 and documento_id is None:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document_to_dict(doc), ensure_ascii=False)
    if hash_sha256:
        (CACHE_DIR / f"{hash_sha256}.json").write_text(payload, encoding="utf-8")
    if documento_id is not None:
        (CACHE_DIR / f"id_{documento_id}.json").write_text(payload, encoding="utf-8")


def _load_cache_file(path: Path) -> Optional[ExtractedDocument]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        doc = document_from_dict(data)
        if doc.text and doc.text.strip():
            return doc
    except Exception:
        return None
    return None


def load_document_text_cache(
    *,
    hash_sha256: Optional[str] = None,
    documento_id: Optional[int] = None,
) -> Optional[ExtractedDocument]:
    if hash_sha256:
        doc = _load_cache_file(CACHE_DIR / f"{hash_sha256}.json")
        if doc:
            return doc
    if documento_id is not None:
        return _load_cache_file(CACHE_DIR / f"id_{documento_id}.json")
    return None


def _try_load_ocr_job(job_id: str) -> Optional[ExtractedDocument]:
    try:
        return load_document_from_job(job_id)
    except (KeyError, ValueError):
        return None


def _scan_ocr_job_for_documento(row: dict[str, Any]) -> tuple[Optional[ExtractedDocument], Optional[str]]:
    """Procura job OCR concluído pelo nome do arquivo ou hash no histórico."""
    nome = (row.get("nome_arquivo") or "").strip().lower()
    for job_meta in list_jobs():
        if job_meta.get("status") != "succeeded":
            continue
        job_id = job_meta.get("job_id")
        if not job_id:
            continue
        fn = (job_meta.get("filename") or "").strip().lower()
        if nome and fn and (nome == fn or nome in fn or fn in nome):
            doc = _try_load_ocr_job(job_id)
            if doc:
                return doc, job_id
    return None, None


def _link_ocr_job_if_missing(documento_id: int, job_id: str, doc: ExtractedDocument) -> None:
    row = get_documento_row(documento_id)
    if not row or row.get("ocr_job_id"):
        return
    update_documento_ocr_done(
        documento_id=documento_id,
        paginas=doc.page_count,
        caracteres=len(doc.text),
        ocr_job_id=job_id,
    )


def load_document_for_id(
    documento_id: int,
    *,
    ocr_job_id: Optional[str] = None,
    texto: Optional[str] = None,
) -> tuple[ExtractedDocument, dict[str, Any], str]:
    """
    Retorna (ExtractedDocument, linha documento, fonte_carregamento).

    fonte: texto_inline | ocr_job | cache | ocr_job_scan
    """
    if texto and len(texto.strip()) >= 50:
        row = get_documento_row(documento_id)
        if not row:
            raise KeyError(f"documento_id {documento_id} não encontrado")
        doc = ExtractedDocument(text=texto.strip(), page_count=row.get("paginas"), page_markers=[])
        save_document_text_cache(doc, hash_sha256=row.get("hash_sha256"), documento_id=documento_id)
        return doc, row, "texto_inline"

    row = get_documento_row(documento_id)
    if not row:
        raise KeyError(f"documento_id {documento_id} não encontrado")

    job_ids_to_try: list[str] = []
    if ocr_job_id:
        job_ids_to_try.append(ocr_job_id)
    if row.get("ocr_job_id"):
        jid = row["ocr_job_id"]
        if jid not in job_ids_to_try:
            job_ids_to_try.append(jid)

    for jid in job_ids_to_try:
        doc = _try_load_ocr_job(jid)
        if doc:
            save_document_text_cache(
                doc, hash_sha256=row.get("hash_sha256"), documento_id=documento_id
            )
            _link_ocr_job_if_missing(documento_id, jid, doc)
            return doc, row, "ocr_job"

    cached = load_document_text_cache(
        hash_sha256=row.get("hash_sha256"),
        documento_id=documento_id,
    )
    if cached:
        return cached, row, "cache"

    doc_scan, job_found = _scan_ocr_job_for_documento(row)
    if doc_scan and job_found:
        save_document_text_cache(
            doc_scan, hash_sha256=row.get("hash_sha256"), documento_id=documento_id
        )
        _link_ocr_job_if_missing(documento_id, job_found, doc_scan)
        return doc_scan, row, "ocr_job_scan"

    # Job existe mas ainda não tem document (pending) — mensagem específica
    for jid in job_ids_to_try:
        data = get_job(jid)
        if data and data.get("status") != "succeeded":
            raise ValueError(
                f"Job OCR {jid} ainda não concluiu (status={data.get('status')}). "
                "Aguarde GET /ocr/jobs/{jid} retornar succeeded."
            )

    raise ValueError(
        f"Não há texto disponível para documento_id={documento_id}. "
        "Opções: (1) envie o campo 'texto' no body com um trecho do material (mín. 50 caracteres); "
        "(2) envie 'ocr_job_id' de um job com status succeeded; "
        "(3) regenere questões com POST /gerar/pdf ou POST /gerar/ocr-job/{{id}} "
        "para gravar cache automático; "
        "(4) faça OCR do PDF escaneado com POST /ocr/pdf."
    )


def merge_extracted_documents(
    docs: list["ExtractedDocument"],
    *,
    labels: Optional[list[str]] = None,
) -> "ExtractedDocument":
    """Concatena vários documentos em um único texto com marcadores de página ajustados."""
    from app.pdf_extractor import ExtractedDocument

    parts: list[str] = []
    markers: list[tuple[int, int]] = []
    offset = 0
    page_count = 0
    for i, doc in enumerate(docs):
        label = labels[i] if labels and i < len(labels) else f"Documento {i + 1}"
        header = f"\n\n--- {label} ---\n\n"
        if parts:
            parts.append(header)
            offset += len(header)
        for m_offset, page_num in doc.page_markers:
            markers.append((offset + m_offset, page_count + page_num))
        parts.append(doc.text)
        offset += len(doc.text)
        page_count += doc.page_count
    return ExtractedDocument(text="".join(parts), page_count=page_count, page_markers=markers)


def ingest_pdf_to_historico(data: bytes, filename: str = "documento.pdf") -> dict[str, Any]:
    """Extrai texto de um PDF nativo e registra/atualiza o documento no histórico (sem IA)."""
    from app.pdf_extractor import extract_text_from_pdf, pdf_has_native_text
    from app.storage import get_documento_by_hash, sha256_bytes, upsert_documento

    if not pdf_has_native_text(data):
        raise ValueError(
            "PDF parece escaneado (sem texto selecionável). "
            "Use o Gerador de Questões com OCR para livros digitalizados."
        )

    hash_id = sha256_bytes(data)
    existente = get_documento_by_hash(hash_id)
    doc = extract_text_from_pdf(data)
    save_document_text_cache(doc, hash_sha256=hash_id)

    documento_id = upsert_documento(
        nome_arquivo=filename,
        hash_sha256=hash_id,
        paginas=doc.page_count,
        caracteres=len(doc.text),
        ocr_job_id=None,
        fonte="pdf_nativo",
    )
    save_document_text_cache(doc, hash_sha256=hash_id, documento_id=documento_id)

    row = get_documento_row(documento_id) or {}
    return {
        "documento_id": documento_id,
        "nome_arquivo": row.get("nome_arquivo") or filename,
        "paginas": doc.page_count,
        "caracteres": len(doc.text),
        "reutilizado": existente is not None,
    }
