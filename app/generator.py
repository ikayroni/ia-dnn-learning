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
from app.document_text import load_document_for_id, save_document_text_cache
from app.storage import (
    get_documento_by_hash,
    get_documento_by_job,
    save_geracao,
    sha256_bytes,
    upsert_documento,
)


def _log(msg: str) -> None:
    from app.console_io import safe_print

    try:
        safe_print(f"[gerador] {msg}")
    except Exception:
        pass


def _attach_questao_ids(questoes: list[Questao], ids: list[int]) -> list[Questao]:
    if len(ids) != len(questoes):
        return questoes
    return [q.model_copy(update={"id": qid}) for q, qid in zip(questoes, ids)]


def _dedupe_key(q: Questao) -> str:
    """Chave que tolera vinhetas com abertura igual, mas pergunta/gabarito diferentes."""
    enun = " ".join(q.enunciado.lower().split())
    gab = (q.gabarito or "").lower().strip()
    if len(enun) <= 260:
        return f"{gab}|{enun}"
    # Caso clínico: o início da vinheta se repete; o final + gabarito distinguem.
    return f"{gab}|{enun[-240:]}"


def _dedupe_questions(questoes: list[Questao]) -> list[Questao]:
    seen_keys: set[str] = set()
    seen_full: set[str] = set()
    unique: list[Questao] = []
    for q in questoes:
        full = " ".join(q.enunciado.lower().split())
        if full in seen_full:
            continue
        key = _dedupe_key(q)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        seen_full.add(full)
        unique.append(q)
    return unique


def _merge_instrucoes(
    base: Optional[str], extra: Optional[str]
) -> Optional[str]:
    parts = [p.strip() for p in (base, extra) if p and p.strip()]
    return "\n".join(parts) if parts else None


def _anti_repeat_instrucoes(questoes: list[Questao]) -> str:
    if not questoes:
        return ""
    lines: list[str] = []
    for q in questoes[-8:]:
        enun = " ".join(q.enunciado.split())
        tail = enun[-220:] if len(enun) > 220 else enun
        lines.append(f"- Gabarito {q.gabarito}: …{tail}")
    return (
        "NÃO repita perguntas já feitas (mesma pergunta final ou mesmo raciocínio).\n"
        "Pode manter a mesma doença do texto, mas mude apresentação clínica e o foco da pergunta.\n"
        "Questões já geradas:\n"
        + "\n".join(lines)
    )


def _batch_size_for_call(
    remaining: int,
    incluir_explicacao: bool,
    num_questoes_por_chunk: int,
) -> int:
    """Lotes menores quando há explicação detalhada — evita truncar o JSON do modelo."""
    cap = 3 if incluir_explicacao else min(5, num_questoes_por_chunk)
    return max(1, min(remaining, cap, num_questoes_por_chunk))


def _questions_for_chunk_pass(
    chunk_idx: int,
    num_chunks: int,
    num_questoes_por_chunk: int,
    num_questoes_total: Optional[int],
    incluir_explicacao: bool,
) -> int:
    if not num_questoes_total or num_questoes_total <= 0:
        return num_questoes_por_chunk
    if num_chunks <= 1:
        return _batch_size_for_call(
            num_questoes_total, incluir_explicacao, num_questoes_por_chunk
        )
    base = num_questoes_total // num_chunks
    extra = num_questoes_total % num_chunks
    target = base + (1 if chunk_idx < extra else 0)
    return _batch_size_for_call(target, incluir_explicacao, num_questoes_por_chunk)


def _invoke_chunk(
    chunk,
    *,
    num_questions: int,
    tipos: Optional[list[str]],
    dificuldade: Optional[str],
    tema: Optional[str],
    instrucoes_extras: Optional[str],
    idioma: str,
    estilo: str,
    num_alternativas: int,
    incluir_explicacao: bool,
    incluir_caso_clinico: Optional[bool],
    temperature: float = 0.3,
) -> list[Questao]:
    result = invoke_bedrock(
        chunk.text,
        num_questions=num_questions,
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
        incluir_caso_clinico=incluir_caso_clinico,
        temperature=temperature,
    )
    questoes = _parse_questoes_from_llm(result)
    for q in questoes:
        if q.idioma is None:
            q.idioma = idioma  # type: ignore[assignment]
        if q.estilo is None:
            q.estilo = estilo  # type: ignore[assignment]
    return questoes


def _generate_sequential(
    chunk,
    *,
    num_questoes_total: int,
    tipos: Optional[list[str]],
    dificuldade: Optional[str],
    tema: Optional[str],
    instrucoes_extras: Optional[str],
    idioma: str,
    estilo: str,
    num_alternativas: int,
    incluir_explicacao: bool,
    incluir_caso_clinico: Optional[bool],
) -> tuple[list[Questao], list[str]]:
    """Uma questão por chamada — confiável para texto curto (1 chunk)."""
    all_questoes: list[Questao] = []
    errors: list[str] = []
    focos = [
        "diagnóstico principal",
        "conduta terapêutica inicial",
        "exame complementar indicado",
        "diagnóstico diferencial",
        "complicação mais provável",
        "prognóstico ou seguimento",
        "farmacologia / mecanismo de ação",
        "critério de gravidade ou internação",
        "prevenção ou fator de risco",
        "interprete de achado laboratorial ou de imagem",
    ]
    max_rounds = max(num_questoes_total * 5, num_questoes_total + 3)
    rounds = 0
    while len(all_questoes) < num_questoes_total and rounds < max_rounds:
        rounds += 1
        n = len(all_questoes) + 1
        foco = focos[(n - 1) % len(focos)]
        extra = (
            _anti_repeat_instrucoes(all_questoes)
            + f"\nGere EXATAMENTE 1 questão inédita (número {n} de {num_questoes_total}). "
            f"Foco desta questão: {foco}. "
            "A pergunta final deve ser claramente diferente das anteriores "
            "(outro aspecto clínico, outro exame, outra conduta ou outro diagnóstico diferencial). "
            "Prefira gabarito diferente dos já usados quando fizer sentido."
        )
        merged = _merge_instrucoes(instrucoes_extras, extra)
        before = len(all_questoes)
        try:
            novas_list = _invoke_chunk(
                chunk,
                num_questions=1,
                tipos=tipos,
                dificuldade=dificuldade,
                tema=tema,
                instrucoes_extras=merged,
                idioma=idioma,
                estilo=estilo,
                num_alternativas=num_alternativas,
                incluir_explicacao=incluir_explicacao,
                incluir_caso_clinico=incluir_caso_clinico,
                temperature=0.55 if rounds > 2 else 0.45,
            )
            all_questoes.extend(novas_list)
            all_questoes = _dedupe_questions(all_questoes)
            gained = len(all_questoes) - before
            if len(novas_list) > 0 and gained == 0:
                _log(
                    f"[sequencial {rounds}] modelo devolveu {len(novas_list)} "
                    f"mas dedupe descartou (enunciado muito parecido)"
                )
            _log(
                f"[sequencial {rounds}] +{gained} questao(oes) "
                f"(total {len(all_questoes)}/{num_questoes_total})"
            )
            if gained == 0:
                errors.append(f"sequencial {rounds}: modelo não devolveu questão nova")
        except Exception as e:
            errors.append(f"sequencial {rounds}: {e}")
            _log(f"[sequencial {rounds}] FALHA: {e}")
    return all_questoes[:num_questoes_total], errors


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
    incluir_caso_clinico: Optional[bool] = None,
    num_questoes_total: Optional[int] = None,
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
        f"inicio: {total} chunk(s) | idioma={idioma} | estilo={estilo} | "
        f"alts={num_alternativas} | explicacao={incluir_explicacao} | "
        f"total_pedido={num_questoes_total}"
    )

    if num_questoes_total and num_questoes_total > 0 and total == 1:
        all_questoes, seq_errors = _generate_sequential(
            chunks[0],
            num_questoes_total=num_questoes_total,
            tipos=tipos,
            dificuldade=dificuldade,
            tema=tema,
            instrucoes_extras=instrucoes_extras,
            idioma=idioma,
            estilo=estilo,
            num_alternativas=num_alternativas,
            incluir_explicacao=incluir_explicacao,
            incluir_caso_clinico=incluir_caso_clinico,
        )
        errors.extend(seq_errors)
    else:
        for idx, chunk in enumerate(chunks, start=1):
            t0 = time.time()
            ask = _questions_for_chunk_pass(
                idx - 1,
                total,
                num_questoes_por_chunk,
                num_questoes_total,
                incluir_explicacao,
            )
            _log(
                f"[chunk {idx}/{total}] {len(chunk.text)} chars | pags "
                f"{chunk.page_start}-{chunk.page_end} | pedindo {ask} questao(oes)"
            )
            try:
                novas_list = _invoke_chunk(
                    chunk,
                    num_questions=ask,
                    tipos=tipos,
                    dificuldade=dificuldade,
                    tema=tema,
                    instrucoes_extras=instrucoes_extras,
                    idioma=idioma,
                    estilo=estilo,
                    num_alternativas=num_alternativas,
                    incluir_explicacao=incluir_explicacao,
                    incluir_caso_clinico=incluir_caso_clinico,
                )
                all_questoes.extend(novas_list)
                _log(
                    f"[chunk {idx}/{total}] OK | {len(novas_list)} questao(oes) | "
                    f"{time.time() - t0:.1f}s"
                )
            except Exception as e:
                errors.append(f"chunk {chunk.chunk_id}: {e}")
                _log(f"[chunk {idx}/{total}] FALHA: {e}")

        all_questoes = _dedupe_questions(all_questoes)

        refill_attempts = 0
        max_refill = 12
        stagnant = 0
        while (
            num_questoes_total
            and len(all_questoes) < num_questoes_total
            and refill_attempts < max_refill
            and chunks
        ):
            remaining = num_questoes_total - len(all_questoes)
            chunk = chunks[refill_attempts % len(chunks)]
            batch = _batch_size_for_call(
                remaining, incluir_explicacao, num_questoes_por_chunk
            )
            merged = _merge_instrucoes(
                instrucoes_extras, _anti_repeat_instrucoes(all_questoes)
            )
            before = len(all_questoes)
            try:
                novas_list = _invoke_chunk(
                    chunk,
                    num_questions=batch,
                    tipos=tipos,
                    dificuldade=dificuldade,
                    tema=tema,
                    instrucoes_extras=merged,
                    idioma=idioma,
                    estilo=estilo,
                    num_alternativas=num_alternativas,
                    incluir_explicacao=incluir_explicacao,
                    incluir_caso_clinico=incluir_caso_clinico,
                    temperature=0.45,
                )
                all_questoes.extend(novas_list)
                all_questoes = _dedupe_questions(all_questoes)
                gained = len(all_questoes) - before
                _log(
                    f"[refill {refill_attempts + 1}] +{gained} questao(oes) "
                    f"(total {len(all_questoes)}/{num_questoes_total})"
                )
                if gained == 0:
                    stagnant += 1
                    if stagnant >= 4:
                        break
                else:
                    stagnant = 0
            except Exception as e:
                errors.append(f"refill {refill_attempts + 1}: {e}")
                _log(f"[refill {refill_attempts + 1}] FALHA: {e}")
                stagnant += 1
                if stagnant >= 4:
                    break
            refill_attempts += 1

    all_questoes = _dedupe_questions(all_questoes)
    if num_questoes_total is not None and num_questoes_total > 0:
        all_questoes = all_questoes[:num_questoes_total]
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
        "incluir_caso_clinico": incluir_caso_clinico,
        "num_questoes_total": num_questoes_total,
        "erros": errors if errors else None,
    }
    return all_questoes, meta


def generate_from_text_persisted(
    text: str,
    *,
    nome_material: str = "testo_colato.txt",
    **kwargs,
) -> tuple[list[Questao], dict]:
    """Gera questões a partir de texto colado e persiste no banco (como PDF)."""
    questoes, meta = generate_from_text(text, **kwargs)

    hash_id = sha256_bytes(text.encode("utf-8"))
    existente = get_documento_by_hash(hash_id)
    documento_id = int(existente["id"]) if existente else None

    doc = ExtractedDocument(text=text, page_count=1, page_markers=[(0, 1)])
    save_document_text_cache(doc, hash_sha256=hash_id)

    if documento_id is None:
        documento_id = upsert_documento(
            nome_arquivo=nome_material,
            hash_sha256=hash_id,
            paginas=1,
            caracteres=len(text),
            ocr_job_id=None,
            fonte="texto",
        )
    else:
        upsert_documento(
            nome_arquivo=nome_material,
            hash_sha256=hash_id,
            paginas=1,
            caracteres=len(text),
            ocr_job_id=None,
            fonte="texto",
        )

    save_document_text_cache(doc, hash_sha256=hash_id, documento_id=documento_id)

    parametros = {k: kwargs.get(k) for k in (
        "tema", "palavras_chave", "pagina_inicio", "pagina_fim",
        "tipos", "dificuldade", "instrucoes_extras",
        "num_questoes_por_chunk", "max_chunks",
        "idioma", "estilo", "num_alternativas", "incluir_explicacao",
        "incluir_caso_clinico", "num_questoes_total",
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
        _log(f"geracao texto salva | documento_id={documento_id} geracao_id={geracao_id}")
    except Exception as e:
        meta["storage_error"] = str(e)
    return questoes, meta


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
        _log(f"PDF reaproveitado do historico | documento_id={documento_id}")
    else:
        documento_id = None  # criamos depois com pag_count e chars reais

    doc = extract_text_from_pdf(data)
    save_document_text_cache(doc, hash_sha256=hash_id)
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

    save_document_text_cache(doc, hash_sha256=hash_id, documento_id=documento_id)

    parametros = {k: kwargs.get(k) for k in (
        "tema", "palavras_chave", "pagina_inicio", "pagina_fim",
        "tipos", "dificuldade", "instrucoes_extras",
        "num_questoes_por_chunk", "max_chunks",
        "idioma", "estilo", "num_alternativas", "incluir_explicacao",
        "incluir_caso_clinico",
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
        _log(f"geracao salva | documento_id={documento_id} geracao_id={geracao_id}")
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
    save_document_text_cache(
        doc,
        hash_sha256=documento.get("hash_sha256") if documento else None,
        documento_id=documento_id,
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
    except Exception as e:
        meta["storage_error"] = str(e)
    return questoes, meta


def discover_topics_from_ocr_job(job_id: str, max_topics: int = 10) -> dict:
    doc = load_document_from_job(job_id)
    info = discover_topics_from_document(doc, max_topics=max_topics)
    info["ocr_job_id"] = job_id
    return info


def generate_from_multi_documento_ids(
    documento_ids: list[int],
    **kwargs,
) -> tuple[list[Questao], dict]:
    """Combina textos de vários documentos do histórico e gera questões em lote."""
    from app.document_text import load_document_for_id, merge_extracted_documents

    if not documento_ids:
        raise ValueError("Informe ao menos um documento_id")
    if len(documento_ids) > 20:
        raise ValueError("Máximo de 20 documentos por geração")

    docs = []
    labels: list[str] = []
    rows_meta: list[dict] = []
    for did in documento_ids:
        doc, row, fonte = load_document_for_id(did)
        docs.append(doc)
        nome = (row.get("nome_arquivo") or f"doc-{did}").strip()
        labels.append(nome)
        rows_meta.append({"documento_id": did, "fonte": fonte, "nome_arquivo": nome})

    merged = merge_extracted_documents(docs, labels=labels)
    ids_label = ",".join(str(i) for i in documento_ids)
    questoes, meta = generate_from_document(
        merged,
        ocr_source=f"multi_documento:{ids_label}",
        **kwargs,
    )
    primary_id = int(documento_ids[0])
    meta["documento_ids"] = documento_ids
    meta["documento_id"] = primary_id
    meta["documentos"] = rows_meta

    parametros = {k: kwargs.get(k) for k in (
        "tema", "palavras_chave", "pagina_inicio", "pagina_fim",
        "tipos", "dificuldade", "instrucoes_extras",
        "num_questoes_por_chunk", "max_chunks",
        "idioma", "estilo", "num_alternativas", "incluir_explicacao",
        "incluir_caso_clinico", "num_questoes_total",
    )}
    parametros["documento_ids"] = documento_ids
    try:
        geracao_id, questao_ids = save_geracao(
            documento_id=primary_id,
            questoes=questoes,
            meta=meta,
            parametros=parametros,
        )
        meta["geracao_id"] = geracao_id
        questoes = _attach_questao_ids(questoes, questao_ids)
    except Exception as e:
        meta["storage_error"] = str(e)
    return questoes, meta


def generate_from_documento_id(documento_id: int, **kwargs) -> tuple[list[Questao], dict]:
    """Reusa um documento já salvo: OCR, cache em disco ou texto via kwargs['texto']."""
    texto_kw = kwargs.pop("texto", None)
    ocr_job_kw = kwargs.pop("ocr_job_id", None)
    doc, row, fonte = load_document_for_id(
        documento_id, ocr_job_id=ocr_job_kw, texto=texto_kw
    )
    ocr_job_id = row.get("ocr_job_id") or ocr_job_kw
    questoes, meta = generate_from_document(
        doc,
        ocr_source=f"textract_job:{ocr_job_id}" if ocr_job_id else f"documento:{fonte}",
        **kwargs,
    )
    meta["ocr_job_id"] = ocr_job_id
    meta["fonte_texto"] = fonte
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
