"""Geração de flash cards a partir do material (texto, PDF, OCR ou documento salvo).

Espelha o fluxo do `generator.py` (questões): faz chunk do texto, chama o Bedrock
por trecho, dedupe e persiste como um deck de flashcards.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from app.bedrock import gerar_flashcards as bedrock_gerar_flashcards
from app.chunker import chunk_text, filter_chunks
from app.config import effective_bedrock_model_id, settings
from app.document_text import load_document_for_id, save_document_text_cache
from app.flashcards_storage import get_deck, save_deck
from app.ocr_jobs import load_document_from_job
from app.pdf_extractor import (
    ExtractedDocument,
    extract_text_from_pdf,
    pdf_has_native_text,
)
from app.storage import (
    get_documento_by_hash,
    get_documento_by_job,
    sha256_bytes,
    upsert_documento,
)


def _log(msg: str) -> None:
    from app.console_io import safe_print

    try:
        safe_print(f"[flashcards] {msg}")
    except Exception:
        pass


def _dedupe(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in cards:
        frente = str(c.get("frente") or "").strip()
        if not frente or not str(c.get("verso") or "").strip():
            continue
        key = " ".join(frente.lower().split())[:160]
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique


def _normalize_card(c: dict[str, Any], idioma: str) -> dict[str, Any]:
    tags = c.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    elif not isinstance(tags, list):
        tags = []
    fonte = c.get("fonte")
    if not isinstance(fonte, dict):
        fonte = None
    return {
        "frente": str(c.get("frente") or "").strip(),
        "verso": str(c.get("verso") or "").strip(),
        "dica": (c.get("dica") or None),
        "tags": tags,
        "dificuldade": c.get("dificuldade"),
        "referencia": c.get("referencia"),
        "fonte": fonte,
    }


def _generate_cards_from_text(
    text: str,
    *,
    num_flashcards_por_chunk: int = 5,
    max_chunks: Optional[int] = None,
    page_markers: Optional[list[tuple[int, int]]] = None,
    page_count: Optional[int] = None,
    tema: Optional[str] = None,
    palavras_chave: Optional[list[str]] = None,
    pagina_inicio: Optional[int] = None,
    pagina_fim: Optional[int] = None,
    instrucoes_extras: Optional[str] = None,
    idioma: str = "pt",
) -> tuple[list[dict[str, Any]], dict]:
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
                "Nenhum trecho casou com o filtro (palavras-chave/páginas). Afrouxe os filtros."
            )

    limit = max_chunks or settings.max_chunks_per_request
    chunks_filtrados_total = len(chunks)
    truncated = chunks_filtrados_total > limit
    if truncated:
        chunks = chunks[:limit]

    all_cards: list[dict[str, Any]] = []
    errors: list[str] = []
    total = len(chunks)
    t_start = time.time()
    _log(f"inicio: {total} chunk(s) | idioma={idioma} | por_chunk={num_flashcards_por_chunk}")

    for idx, chunk in enumerate(chunks, start=1):
        t0 = time.time()
        _log(f"[chunk {idx}/{total}] {len(chunk.text)} chars | enviando ao Bedrock")
        try:
            result = bedrock_gerar_flashcards(
                chunk.text,
                num_flashcards=num_flashcards_por_chunk,
                chunk_id=chunk.chunk_id,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                tema=tema,
                instrucoes_extras=instrucoes_extras,
                idioma=idioma,
            )
            novas = 0
            for raw in result.get("flashcards", []):
                card = _normalize_card(raw, idioma)
                if card["frente"] and card["verso"]:
                    all_cards.append(card)
                    novas += 1
            _log(f"[chunk {idx}/{total}] OK | {novas} card(s) | {time.time() - t0:.1f}s")
        except Exception as e:  # noqa: BLE001
            errors.append(f"chunk {chunk.chunk_id}: {e}")
            _log(f"[chunk {idx}/{total}] FALHA: {e}")

    all_cards = _dedupe(all_cards)
    _log(f"fim: {len(all_cards)} card(s) unicos em {time.time() - t_start:.1f}s (erros={len(errors)})")

    if not all_cards:
        raise RuntimeError(
            "Nenhum flashcard foi gerado. Verifique o material/tema ou tente novamente. "
            + (f"Erros: {errors[0]}" if errors else "")
        )

    meta = {
        "chunks_processados": len(chunks),
        "chunks_filtrados": chunks_filtrados_total,
        "chunks_total": total_chunks,
        "truncado": truncated,
        "paginas": page_count,
        "flashcards_gerados": len(all_cards),
        "modelo": effective_bedrock_model_id(),
        "tema": tema,
        "palavras_chave": palavras_chave or None,
        "intervalo_paginas": [pagina_inicio, pagina_fim] if (pagina_inicio or pagina_fim) else None,
        "filtro": filtro_info or None,
        "idioma": idioma,
        "num_flashcards_por_chunk": num_flashcards_por_chunk,
        "erros": errors if errors else None,
    }
    return all_cards, meta


def _format_material_nome(nome: str) -> str:
    """Transforma nome de arquivo técnico em título legível para o baralho."""
    import re

    s = (nome or "").strip()
    if not s:
        return "Baralho"
    if s.lower().endswith(".pdf"):
        s = s[:-4]
    # Sufixos comuns: _2025-1572-1587 ou -1572-1587 (intervalo de páginas)
    s = re.sub(r"[-_]\d{4}-\d{1,5}-\d{1,5}$", "", s)
    s = re.sub(r"[-_]\d{1,5}-\d{1,5}$", "", s)
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return "Baralho"
    small = {"de", "da", "do", "das", "dos", "e", "and", "of", "the", "a", "an", "in", "on"}
    words = s.split()
    titled = []
    for i, w in enumerate(words):
        lw = w.lower()
        if i > 0 and lw in small:
            titled.append(lw)
        elif lw.isdigit() or re.match(r"^\d+(st|nd|rd|th)$", lw):
            titled.append(lw)
        else:
            titled.append(lw[:1].upper() + lw[1:] if lw else lw)
    s = " ".join(titled)
    if len(s) > 72:
        cut = s[:69].rsplit(" ", 1)[0]
        s = f"{cut}…" if cut else s[:69] + "…"
    return s


def _default_titulo(tema: Optional[str], nome_arquivo: Optional[str]) -> str:
    if tema and tema.strip():
        return tema.strip()
    if nome_arquivo:
        return _format_material_nome(nome_arquivo)
    return "Baralho de flashcards"


def generate_flashcards_from_text(
    text: str,
    *,
    titulo: Optional[str] = None,
    idioma: str = "pt",
    **kwargs,
) -> dict[str, Any]:
    cards, meta = _generate_cards_from_text(text, idioma=idioma, **kwargs)
    deck_id = save_deck(
        titulo=titulo or _default_titulo(kwargs.get("tema"), None),
        cards=cards,
        documento_id=None,
        tema=kwargs.get("tema"),
        idioma=idioma,
        fonte="ia_texto",
        modelo=meta.get("modelo"),
        meta=meta,
    )
    meta["deck_id"] = deck_id
    return {"deck": get_deck(deck_id), "meta": meta}


def generate_flashcards_from_pdf_bytes(
    data: bytes,
    *,
    filename: str = "documento.pdf",
    titulo: Optional[str] = None,
    idioma: str = "pt",
    **kwargs,
) -> dict[str, Any]:
    if not pdf_has_native_text(data):
        raise ValueError(
            "PDF parece escaneado (sem texto nativo selecionável). "
            "Use o fluxo de OCR: POST /ocr/pdf → aguarde succeeded → "
            "POST /flashcards/gerar/ocr-job/{job_id}."
        )

    hash_id = sha256_bytes(data)
    existente = get_documento_by_hash(hash_id)
    documento_id = int(existente["id"]) if existente else None

    doc = extract_text_from_pdf(data)
    save_document_text_cache(doc, hash_sha256=hash_id)
    cards, meta = _generate_cards_from_text(
        doc.text,
        page_markers=doc.page_markers,
        page_count=doc.page_count,
        idioma=idioma,
        **kwargs,
    )

    if documento_id is None:
        documento_id = upsert_documento(
            nome_arquivo=filename,
            hash_sha256=hash_id,
            paginas=doc.page_count,
            caracteres=len(doc.text),
            ocr_job_id=None,
            fonte="pdf_nativo",
        )
    save_document_text_cache(doc, hash_sha256=hash_id, documento_id=documento_id)

    deck_id = save_deck(
        titulo=titulo or _default_titulo(kwargs.get("tema"), filename),
        cards=cards,
        documento_id=documento_id,
        tema=kwargs.get("tema"),
        idioma=idioma,
        fonte="ia_pdf",
        modelo=meta.get("modelo"),
        meta=meta,
    )
    meta["deck_id"] = deck_id
    meta["documento_id"] = documento_id
    return {"deck": get_deck(deck_id), "meta": meta}


def generate_flashcards_from_ocr_job(
    job_id: str,
    *,
    titulo: Optional[str] = None,
    idioma: str = "pt",
    **kwargs,
) -> dict[str, Any]:
    doc = load_document_from_job(job_id)
    documento = get_documento_by_job(job_id)
    documento_id = documento["id"] if documento else None
    nome = documento.get("nome_arquivo") if documento else None

    cards, meta = _generate_cards_from_text(
        doc.text,
        page_markers=doc.page_markers,
        page_count=doc.page_count,
        idioma=idioma,
        **kwargs,
    )
    meta["ocr_job_id"] = job_id
    save_document_text_cache(
        doc,
        hash_sha256=documento.get("hash_sha256") if documento else None,
        documento_id=documento_id,
    )
    deck_id = save_deck(
        titulo=titulo or _default_titulo(kwargs.get("tema"), nome),
        cards=cards,
        documento_id=documento_id,
        tema=kwargs.get("tema"),
        idioma=idioma,
        fonte="ia_ocr",
        modelo=meta.get("modelo"),
        meta=meta,
    )
    meta["deck_id"] = deck_id
    meta["documento_id"] = documento_id
    return {"deck": get_deck(deck_id), "meta": meta}


def generate_flashcards_from_documento_id(
    documento_id: int,
    *,
    titulo: Optional[str] = None,
    idioma: str = "pt",
    **kwargs,
) -> dict[str, Any]:
    texto_kw = kwargs.pop("texto", None)
    ocr_job_kw = kwargs.pop("ocr_job_id", None)
    doc, row, fonte = load_document_for_id(
        documento_id, ocr_job_id=ocr_job_kw, texto=texto_kw
    )
    cards, meta = _generate_cards_from_text(
        doc.text,
        page_markers=doc.page_markers,
        page_count=doc.page_count,
        idioma=idioma,
        **kwargs,
    )
    meta["documento_id"] = documento_id
    meta["fonte_texto"] = fonte
    deck_id = save_deck(
        titulo=titulo or _default_titulo(kwargs.get("tema"), row.get("nome_arquivo")),
        cards=cards,
        documento_id=documento_id,
        tema=kwargs.get("tema"),
        idioma=idioma,
        fonte="ia_documento",
        modelo=meta.get("modelo"),
        meta=meta,
    )
    meta["deck_id"] = deck_id
    return {"deck": get_deck(deck_id), "meta": meta}
