"""Fila de estudo estilo Anki: etapa atual + flashcards due + bloco de questões."""

from __future__ import annotations

from typing import Any, Optional

from app.flashcards_storage import count_cards_trilha, get_due_cards_trilha
from app.trilha_storage import get_etapa_atual, get_trilha


def _etapa_tem_leitura(etapa: dict[str, Any]) -> bool:
    return bool(
        (etapa.get("conteudo") or "").strip()
        or (etapa.get("objetivo") or "").strip()
        or (etapa.get("titulo") or "").strip()
    )


def _etapa_tem_questoes(trilha: dict[str, Any], etapa: dict[str, Any]) -> bool:
    if not trilha.get("documento_id"):
        return False
    return bool(
        etapa.get("tema")
        or etapa.get("pagina_inicio") is not None
        or etapa.get("palavras_chave")
    )


def get_estudo_stats(trilha_id: int) -> Optional[dict[str, Any]]:
    trilha = get_trilha(trilha_id, include_etapas=False)
    if not trilha:
        return None

    doc_id = trilha.get("documento_id")
    cards = count_cards_trilha(trilha_id=trilha_id, documento_id=doc_id)
    etapa = get_etapa_atual(trilha_id)

    ler = 0
    questoes = 0
    etapa_titulo = None
    if etapa and etapa.get("status") != "concluida":
        etapa_titulo = etapa.get("titulo")
        if _etapa_tem_leitura(etapa):
            ler = 1
        if _etapa_tem_questoes(trilha, etapa):
            questoes = 1

    itens_hoje = cards["cards_due"] + ler + questoes

    return {
        "trilha_id": trilha_id,
        "cards_total": cards["cards_total"],
        "cards_due": cards["cards_due"],
        "cards_novos": cards["cards_novos"],
        "etapa_pendente": bool(etapa and etapa.get("status") != "concluida"),
        "etapa_atual_titulo": etapa_titulo,
        "itens_hoje": itens_hoje,
    }


def montar_fila_estudo(trilha_id: int, *, limit: int = 30) -> Optional[dict[str, Any]]:
    """Monta fila mista: leitura da etapa → flashcards due → questões."""
    trilha = get_trilha(trilha_id)
    if not trilha:
        return None

    doc_id = trilha.get("documento_id")
    stats = get_estudo_stats(trilha_id)
    if not stats:
        return None

    itens: list[dict[str, Any]] = []
    etapa = get_etapa_atual(trilha_id)

    if etapa and etapa.get("status") != "concluida" and _etapa_tem_leitura(etapa):
        itens.append(
            {
                "tipo": "ler",
                "etapa_id": int(etapa["id"]),
                "ordem_etapa": int(etapa["ordem"]),
                "titulo": etapa.get("titulo") or f"Etapa {etapa['ordem']}",
                "modulo": etapa.get("modulo"),
                "objetivo": etapa.get("objetivo"),
                "conteudo": etapa.get("conteudo"),
                "tema": etapa.get("tema"),
                "pagina_inicio": etapa.get("pagina_inicio"),
                "pagina_fim": etapa.get("pagina_fim"),
                "palavras_chave": etapa.get("palavras_chave") or [],
                "duracao_minutos": etapa.get("duracao_minutos"),
            }
        )

    cards_limit = max(1, limit - len(itens) - 1)
    for card in get_due_cards_trilha(
        trilha_id=trilha_id, documento_id=doc_id, limit=cards_limit
    ):
        itens.append(
            {
                "tipo": "flashcard",
                "flashcard_id": card["id"],
                "deck_id": card["deck_id"],
                "frente": card["frente"],
                "verso": card["verso"],
                "dica": card.get("dica"),
                "referencia": card.get("referencia"),
                "tags": card.get("tags") or [],
            }
        )

    if (
        etapa
        and etapa.get("status") != "concluida"
        and _etapa_tem_questoes(trilha, etapa)
        and len(itens) < limit
    ):
        itens.append(
            {
                "tipo": "questoes",
                "etapa_id": int(etapa["id"]),
                "titulo": f"Questões — {etapa.get('titulo') or 'etapa atual'}",
                "documento_id": int(doc_id),
                "tema": etapa.get("tema"),
                "pagina_inicio": etapa.get("pagina_inicio"),
                "pagina_fim": etapa.get("pagina_fim"),
                "num_questoes": 5,
                "estilo": "clinico",
            }
        )

    return {
        "trilha_id": trilha_id,
        "titulo": trilha.get("titulo"),
        "documento_id": doc_id,
        "etapa_atual": trilha.get("etapa_atual"),
        "total_itens": len(itens),
        "cards_due": stats["cards_due"],
        "cards_novos": stats["cards_novos"],
        "itens_hoje": stats["itens_hoje"],
        "itens": itens,
    }
