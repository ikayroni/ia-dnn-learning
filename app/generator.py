from __future__ import annotations

import sys
import time
from typing import Optional

from app.bedrock import discover_topics, invoke_bedrock
from app.chunker import chunk_text, filter_chunks
from app.config import effective_bedrock_model_id, settings
from app.ocr_jobs import load_document_from_job
from app.pdf_extractor import ExtractedDocument, extract_text_from_pdf
from app.schemas import Questao
from app.storage import (
    get_documento_by_hash,
    get_documento_by_job,
    save_geracao,
    sha256_bytes,
    upsert_documento,
)


def _log(msg: str) -> None:
    print(f"[gerador] {msg}", flush=True, file=sys.stdout)


def _attach_questao_ids(questoes: list[Questao], ids: list[int]) -> list[Questao]:
    if len(ids) != len(questoes):
        return questoes
    return [q.model_copy(update={"id": qid}) for q, qid in zip(questoes, ids)]


def _dedupe_questions(questoes: list[Questao]) -> list[Questao]:
    seen: set[str] = set()
    unique: list[Questao] = []
    for q in questoes:
        key = " ".join(q.enunciado.lower().split())[:200]
        if key in seen:
            continue
        seen.add(key)
        unique.append(q)
    return unique


def _parse_questoes_from_llm(data: dict) -> list[Questao]:
    raw_list = data.get("questoes", [])
    return [Questao.model_validate(item) for item in raw_list]


def generate_from_text(
    text: str,
    *,
    num_questoes_por_chunk: int = 2,
    tipos: Optional[list[str]] = None,
    dificuldade: Optional[str] = None,
    max_chunks: Optional[int] = None,
    page_markers: Optional[list[tuple[int, int]]] = None,
    page_count: Optional[int] = None,
    ocr_source: Optional[str] = None,
    tema: Optional[str] = None,
    palavras_chave: Optional[list[str]] = None,
    pagina_inicio: Optional[int] = None,
    pagina_fim: Optional[int] = None,
    instrucoes_extras: Optional[str] = None,
    idioma: str = "pt",
    estilo: str = "clinico",
    num_alternativas: int = 5,
    incluir_explicacao: bool = True,
) -> tuple[list[Questao], dict]:
    chunks = chunk_text(
        text,
        chunk_size=settings.chunk_size_chars,
        overlap=settings.chunk_overlap_chars,
        page_markers=page_markers,
    )

    if not chunks:
        raise ValueError("Texto vazio após processamento.")

    total_chunks = len(chunks)
    filtro_info: dict = {}

    if palavras_chave or pagina_inicio or pagina_fim:
        chunks, filtro_info = filter_chunks(
            chunks,
            keywords=palavras_chave,
            pagina_inicio=pagina_inicio,
            pagina_fim=pagina_fim,
        )
        if not chunks:
            raise ValueError(
                "Nenhum trecho casou com o filtro (palavras-chave/páginas). "
                "Afrouxe os filtros."
            )

    limit = max_chunks or settings.max_chunks_per_request
    chunks_filtrados_total = len(chunks)
    if chunks_filtrados_total > limit:
        chunks = chunks[:limit]
        truncated = True
    else:
        truncated = False

    all_questoes: list[Questao] = []
    errors: list[str] = []
    total = len(chunks)
    t_start = time.time()
    _log(
        f"inicio: {total} chunk(s) · idioma={idioma} · estilo={estilo} · "
        f"alts={num_alternativas} · explicacao={incluir_explicacao}"
    )

    for idx, chunk in enumerate(chunks, start=1):
        t0 = time.time()
        _log(
            f"[chunk {idx}/{total}] {len(chunk.text)} chars · pags "
            f"{chunk.page_start}-{chunk.page_end} → enviando ao Bedrock…"
        )
        try:
            result = invoke_bedrock(
                chunk.text,
                num_questions=num_questoes_por_chunk,
                tipos=tipos,
                dificuldade=dificuldade,
                chunk_id=chunk.chunk_id,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                tema=tema,
                instrucoes_extras=instrucoes_extras,
                idioma=idioma,
                estilo=estilo,
                num_alternativas=num_alternativas,
                incluir_explicacao=incluir_explicacao,
            )
            novas = 0
            for q in _parse_questoes_from_llm(result):
                if q.idioma is None:
                    q.idioma = idioma  # type: ignore[assignment]
                if q.estilo is None:
                    q.estilo = estilo  # type: ignore[assignment]
                all_questoes.append(q)
                novas += 1
            _log(f"[chunk {idx}/{total}] OK · {novas} questao(oes) · {time.time() - t0:.1f}s")
        except Exception as e:
            errors.append(f"chunk {chunk.chunk_id}: {e}")
            _log(f"[chunk {idx}/{total}] FALHA: {e}")

    all_questoes = _dedupe_questions(all_questoes)
    _log(
        f"fim: {len(all_questoes)} questao(oes) unicas em {time.time() - t_start:.1f}s "
        f"(erros={len(errors)})"
    )

    meta = {
        "chunks_processados": len(chunks),
        "chunks_filtrados": chunks_filtrados_total,
        "chunks_total": total_chunks,
        "truncado": truncated,
        "paginas": page_count,
        "questoes_geradas": len(all_questoes),
        "modelo": effective_bedrock_model_id(),
        "ocr_source": ocr_source,
        "tema": tema,
        "palavras_chave": palavras_chave or None,
        "intervalo_paginas": [pagina_inicio, pagina_fim] if (pagina_inicio or pagina_fim) else None,
        "filtro": filtro_info or None,
        "idioma": idioma,
        "estilo": estilo,
        "num_alternativas": num_alternativas,
        "incluir_explicacao": incluir_explicacao,
        "erros": errors if errors else None,
    }
    return all_questoes, meta


def discover_topics_from_document(doc: ExtractedDocument, max_topics: int = 10) -> dict:
    sample = doc.text
    if len(sample) > 8000:
        third = 8000 // 3
        sample = sample[:third] + "\n\n" + sample[len(sample) // 2 : len(sample) // 2 + third] + "\n\n" + sample[-third:]
    temas = discover_topics(sample, max_topics=max_topics)
    return {
        "temas": temas,
        "paginas": doc.page_count,
        "caracteres_amostrados": len(sample),
        "modelo": effective_bedrock_model_id(),
    }


def generate_from_document(
    doc: ExtractedDocument,
    **kwargs,
) -> tuple[list[Questao], dict]:
    questoes, meta = generate_from_text(
        doc.text,
        page_markers=doc.page_markers,
        page_count=doc.page_count,
        **kwargs,
    )
    meta["paginas"] = doc.page_count
    meta["caracteres_extraidos"] = len(doc.text)
    return questoes, meta


def generate_from_pdf_bytes(
    data: bytes,
    *,
    filename: str = "documento.pdf",
    **kwargs,
) -> tuple[list[Questao], dict]:
    from app.pdf_extractor import pdf_has_native_text

    if not pdf_has_native_text(data):
        raise ValueError(
            "PDF parece escaneado (sem texto nativo selecionavel). "
            "Use o fluxo de OCR: POST /ocr/pdf -> aguarde status=succeeded em "
            "GET /ocr/jobs/{job_id} -> POST /gerar/ocr-job/{job_id}."
        )

    hash_id = sha256_bytes(data)
    existente = get_documento_by_hash(hash_id)
    if existente:
        documento_id = int(existente["id"])
        _log(f"PDF reaproveitado do historico · documento_id={documento_id}")
    else:
        documento_id = None  # criamos depois com pag_count e chars reais

    doc = extract_text_from_pdf(data)
    questoes, meta = generate_from_document(doc, **kwargs)

    if documento_id is None:
        documento_id = upsert_documento(
            nome_arquivo=filename,
            hash_sha256=hash_id,
            paginas=doc.page_count,
            caracteres=len(doc.text),
            ocr_job_id=None,
            fonte="pdf_nativo",
        )
    else:
        upsert_documento(
            nome_arquivo=filename,
            hash_sha256=hash_id,
            paginas=doc.page_count,
            caracteres=len(doc.text),
            ocr_job_id=None,
            fonte="pdf_nativo",
        )

    parametros = {k: kwargs.get(k) for k in (
        "tema", "palavras_chave", "pagina_inicio", "pagina_fim",
        "tipos", "dificuldade", "instrucoes_extras",
        "num_questoes_por_chunk", "max_chunks",
        "idioma", "estilo", "num_alternativas", "incluir_explicacao",
    )}
    try:
        geracao_id, questao_ids = save_geracao(
            documento_id=documento_id,
            questoes=questoes,
            meta=meta,
            parametros=parametros,
        )
        meta["geracao_id"] = geracao_id
        meta["documento_id"] = documento_id
        questoes = _attach_questao_ids(questoes, questao_ids)
        _log(f"geracao salva · documento_id={documento_id} geracao_id={geracao_id}")
    except Exception as e:
        meta["storage_error"] = str(e)
    return questoes, meta


def generate_from_ocr_job(
    job_id: str,
    **kwargs,
) -> tuple[list[Questao], dict]:
    doc = load_document_from_job(job_id)
    questoes, meta = generate_from_document(
        doc, ocr_source=f"textract_job:{job_id}", **kwargs
    )
    meta["ocr_job_id"] = job_id

    documento = get_documento_by_job(job_id)
    documento_id = documento["id"] if documento else None
    parametros = {k: kwargs.get(k) for k in (
        "tema", "palavras_chave", "pagina_inicio", "pagina_fim",
        "tipos", "dificuldade", "instrucoes_extras",
        "num_questoes_por_chunk", "max_chunks",
        "idioma", "estilo", "num_alternativas", "incluir_explicacao",
    )}
    try:
        geracao_id, questao_ids = save_geracao(
            documento_id=documento_id,
            questoes=questoes,
            meta=meta,
            parametros=parametros,
        )
        meta["geracao_id"] = geracao_id
        meta["documento_id"] = documento_id
        questoes = _attach_questao_ids(questoes, questao_ids)
    except Exception as e:
        meta["storage_error"] = str(e)
    return questoes, meta


def discover_topics_from_ocr_job(job_id: str, max_topics: int = 10) -> dict:
    doc = load_document_from_job(job_id)
    info = discover_topics_from_document(doc, max_topics=max_topics)
    info["ocr_job_id"] = job_id
    return info


def generate_from_documento_id(documento_id: int, **kwargs) -> tuple[list[Questao], dict]:
    """Reusa um documento já salvo no histórico para gerar novas variações de questões.

    - Se o documento veio de OCR, carrega o texto do job OCR.
    - Caso contrário, exige que o documento tenha o `ocr_job_id` salvo.
    Não recebe bytes do PDF — economiza upload e custo de extração.
    """
    from app.storage import get_documento_row

    row = get_documento_row(documento_id)
    if not row:
        raise KeyError(f"documento_id {documento_id} não encontrado")
    ocr_job_id = row.get("ocr_job_id")
    if not ocr_job_id:
        raise ValueError(
            "Este documento não tem texto OCR persistido para regerar. "
            "Para variações sobre PDF nativo, reenvie o arquivo via POST /gerar/pdf "
            "(o sistema vai reaproveitar o histórico pelo hash do PDF)."
        )
    doc = load_document_from_job(ocr_job_id)
    questoes, meta = generate_from_document(
        doc, ocr_source=f"textract_job:{ocr_job_id}", **kwargs
    )
    meta["ocr_job_id"] = ocr_job_id
    meta["documento_id"] = documento_id

    parametros = {k: kwargs.get(k) for k in (
        "tema", "palavras_chave", "pagina_inicio", "pagina_fim",
        "tipos", "dificuldade", "instrucoes_extras",
        "num_questoes_por_chunk", "max_chunks",
        "idioma", "estilo", "num_alternativas", "incluir_explicacao",
    )}
    try:
        geracao_id, questao_ids = save_geracao(
            documento_id=documento_id,
            questoes=questoes,
            meta=meta,
            parametros=parametros,
        )
        meta["geracao_id"] = geracao_id
        questoes = _attach_questao_ids(questoes, questao_ids)
    except Exception as e:
        meta["storage_error"] = str(e)
    return questoes, meta
