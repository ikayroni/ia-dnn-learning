"""Geração de mapas mentais a partir do material (texto, PDF já salvo, OCR ou múltiplos documentos).

Ao contrário dos flashcards (lista plana, geração por chunk), o mapa mental é uma
visão GLOBAL do conteúdo: usamos uma amostra do texto numa única chamada ao Bedrock
e persistimos a árvore resultante.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from app.bedrock import gerar_mapa_mental as bedrock_gerar_mapa_mental
from app.config import effective_bedrock_model_id
from app.document_text import (
    load_document_for_id,
    merge_extracted_documents,
    save_document_text_cache,
)
from app.mapas_storage import get_mapa, save_mapa


def _log(msg: str) -> None:
    from app.console_io import safe_print

    try:
        safe_print(f"[mapas] {msg}")
    except Exception:
        pass


def _sample_text(text: str, max_len: int = 12000) -> str:
    """Amostra representativa: começo, meio e fim do material."""
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    third = max_len // 3
    meio = len(s) // 2
    return s[:third] + "\n\n[...]\n\n" + s[meio : meio + third] + "\n\n[...]\n\n" + s[-third:]


def _normalize_tree(
    node: dict[str, Any],
    *,
    profundidade: int,
    max_ramos: int,
    max_filhos: int,
    nivel: int = 0,
) -> Optional[dict[str, Any]]:
    """Sanitiza a árvore vinda da IA: título obrigatório, poda profundidade/filhos.

    - nível 0 = raiz; seus filhos diretos (nível 1) são limitados a `max_ramos`;
    - níveis mais profundos são limitados a `max_filhos`;
    - a árvore é cortada em `profundidade` níveis abaixo da raiz.
    """
    if not isinstance(node, dict):
        return None
    titulo = str(node.get("titulo") or "").strip()
    if not titulo:
        return None
    nota = node.get("nota")
    if isinstance(nota, str):
        nota = nota.strip() or None
    elif nota is not None:
        nota = str(nota)

    filhos_out: list[dict[str, Any]] = []
    if nivel < profundidade:
        limite = max_ramos if nivel == 0 else max_filhos
        filhos_raw = node.get("filhos")
        if isinstance(filhos_raw, list):
            for filho in filhos_raw[:limite]:
                norm = _normalize_tree(
                    filho,
                    profundidade=profundidade,
                    max_ramos=max_ramos,
                    max_filhos=max_filhos,
                    nivel=nivel + 1,
                )
                if norm:
                    filhos_out.append(norm)
    return {
        "titulo": titulo[:300],
        "nota": (nota[:2000] if isinstance(nota, str) else None),
        "cor": node.get("cor") or None,
        "filhos": filhos_out,
    }


def _contar_nos(node: dict[str, Any]) -> int:
    return 1 + sum(_contar_nos(f) for f in node.get("filhos", []))


def _gerar_arvore(
    text: str,
    *,
    tema: Optional[str],
    instrucoes_extras: Optional[str],
    idioma: str,
    max_ramos: int,
    profundidade: int,
    max_filhos: int,
    paginas: Optional[int] = None,
) -> tuple[dict[str, Any], dict]:
    if not text or not text.strip():
        raise ValueError("Texto vazio após processamento.")
    t0 = time.time()
    _log(
        f"inicio | idioma={idioma} | ramos={max_ramos} | prof={profundidade} | "
        f"chars={len(text)} | tema={tema or '—'}"
    )
    data = bedrock_gerar_mapa_mental(
        _sample_text(text),
        tema=tema,
        instrucoes_extras=instrucoes_extras,
        idioma=idioma,
        max_ramos=max_ramos,
        profundidade=profundidade,
        max_filhos=max_filhos,
    )
    raiz = _normalize_tree(
        data.get("raiz") or {},
        profundidade=profundidade,
        max_ramos=max_ramos,
        max_filhos=max_filhos,
    )
    if not raiz:
        raise RuntimeError("A IA não retornou um mapa mental válido. Tente novamente.")

    total = _contar_nos(raiz)
    _log(f"fim | {total} nó(s) em {time.time() - t0:.1f}s")
    meta = {
        "modelo": effective_bedrock_model_id(),
        "tema": tema,
        "idioma": idioma,
        "max_ramos": max_ramos,
        "profundidade": profundidade,
        "max_filhos": max_filhos,
        "total_nos": total,
        "paginas": paginas,
        "titulo_ia": data.get("titulo"),
    }
    return raiz, meta


def generate_mapa_from_text(
    text: str,
    *,
    titulo: Optional[str] = None,
    tema: Optional[str] = None,
    idioma: str = "pt",
    max_ramos: int = 6,
    profundidade: int = 3,
    max_filhos: int = 5,
    instrucoes_extras: Optional[str] = None,
) -> dict[str, Any]:
    raiz, meta = _gerar_arvore(
        text,
        tema=tema,
        instrucoes_extras=instrucoes_extras,
        idioma=idioma,
        max_ramos=max_ramos,
        profundidade=profundidade,
        max_filhos=max_filhos,
    )
    mapa_id = save_mapa(
        titulo=titulo or meta.get("titulo_ia") or (tema or raiz["titulo"]),
        raiz=raiz,
        documento_id=None,
        tema=tema,
        idioma=idioma,
        fonte="ia_texto",
        modelo=meta.get("modelo"),
        meta=meta,
    )
    meta["mapa_id"] = mapa_id
    return {"mapa": get_mapa(mapa_id), "meta": meta}


def generate_mapa_from_documento_id(
    documento_id: int,
    *,
    titulo: Optional[str] = None,
    tema: Optional[str] = None,
    idioma: str = "pt",
    max_ramos: int = 6,
    profundidade: int = 3,
    max_filhos: int = 5,
    instrucoes_extras: Optional[str] = None,
    texto: Optional[str] = None,
    ocr_job_id: Optional[str] = None,
) -> dict[str, Any]:
    doc, row, fonte = load_document_for_id(documento_id, ocr_job_id=ocr_job_id, texto=texto)
    raiz, meta = _gerar_arvore(
        doc.text,
        tema=tema,
        instrucoes_extras=instrucoes_extras,
        idioma=idioma,
        max_ramos=max_ramos,
        profundidade=profundidade,
        max_filhos=max_filhos,
        paginas=doc.page_count,
    )
    save_document_text_cache(
        doc,
        hash_sha256=row.get("hash_sha256"),
        documento_id=documento_id,
    )
    meta["documento_id"] = documento_id
    meta["fonte_texto"] = fonte
    mapa_id = save_mapa(
        titulo=titulo or meta.get("titulo_ia") or (tema or _nome_para_titulo(row.get("nome_arquivo"))),
        raiz=raiz,
        documento_id=documento_id,
        tema=tema,
        idioma=idioma,
        fonte="ia_documento",
        modelo=meta.get("modelo"),
        meta=meta,
    )
    meta["mapa_id"] = mapa_id
    return {"mapa": get_mapa(mapa_id), "meta": meta}


def generate_mapa_from_multi_documento_ids(
    documento_ids: list[int],
    *,
    titulo: Optional[str] = None,
    tema: Optional[str] = None,
    idioma: str = "pt",
    max_ramos: int = 6,
    profundidade: int = 3,
    max_filhos: int = 5,
    instrucoes_extras: Optional[str] = None,
) -> dict[str, Any]:
    if not documento_ids:
        raise ValueError("Informe ao menos um documento_id")
    if len(documento_ids) > 20:
        raise ValueError("Máximo de 20 documentos por geração")

    docs = []
    labels: list[str] = []
    rows_meta: list[dict] = []
    primary_row: dict | None = None
    for did in documento_ids:
        doc, row, fonte = load_document_for_id(did)
        docs.append(doc)
        nome = (row.get("nome_arquivo") or f"doc-{did}").strip()
        labels.append(nome)
        rows_meta.append({"documento_id": did, "fonte": fonte, "nome_arquivo": nome})
        if primary_row is None:
            primary_row = row

    merged = merge_extracted_documents(docs, labels=labels)
    raiz, meta = _gerar_arvore(
        merged.text,
        tema=tema,
        instrucoes_extras=instrucoes_extras,
        idioma=idioma,
        max_ramos=max_ramos,
        profundidade=profundidade,
        max_filhos=max_filhos,
        paginas=merged.page_count,
    )
    primary_id = int(documento_ids[0])
    meta["documento_ids"] = documento_ids
    meta["documento_id"] = primary_id
    meta["documentos"] = rows_meta
    meta["fonte_texto"] = "multi_documento"
    default_titulo = titulo or meta.get("titulo_ia") or (
        tema or _nome_para_titulo(primary_row.get("nome_arquivo") if primary_row else None)
    )
    if len(documento_ids) > 1 and not titulo:
        default_titulo = f"{default_titulo} (+{len(documento_ids) - 1} docs)"
    mapa_id = save_mapa(
        titulo=default_titulo,
        raiz=raiz,
        documento_id=primary_id,
        tema=tema,
        idioma=idioma,
        fonte="ia_multi_documento",
        modelo=meta.get("modelo"),
        meta=meta,
    )
    meta["mapa_id"] = mapa_id
    return {"mapa": get_mapa(mapa_id), "meta": meta}


def _nome_para_titulo(nome: Optional[str]) -> str:
    if not nome:
        return "Mapa mental"
    s = nome.strip()
    if s.lower().endswith(".pdf"):
        s = s[:-4]
    s = s.replace("_", " ").replace("-", " ").strip()
    return (s[:72] + "…") if len(s) > 72 else (s or "Mapa mental")
