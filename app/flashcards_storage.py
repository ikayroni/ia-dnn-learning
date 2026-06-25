"""Persistência de flash cards: decks, cards e revisões (repetição espaçada SM-2)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.db import connect, init_db
from app.schemas import Flashcard

# ---------------------------------------------------------------------------
# Repetição espaçada — SM-2 (SuperMemo 2), o mesmo núcleo usado pelo Anki.
# As 4 notas da UI mapeiam para a "qualidade" q do SM-2:
#   0 Errei (again) -> q=0   | 1 Difícil (hard) -> q=3
#   2 Bom (good)    -> q=4   | 3 Fácil (easy)   -> q=5
# ---------------------------------------------------------------------------
_NOTA_TO_Q = {0: 0, 1: 3, 2: 4, 3: 5}
_EASE_MIN = 1.3
# "Errei" volta na mesma sessão (poucos minutos depois) em vez de só amanhã.
_AGAIN_MINUTES = 10


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime) -> str:
    # SQLite guarda em UTC sem tz-info, alinhado a datetime('now').
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def sm2_next(
    *,
    nota: int,
    repeticoes: int,
    intervalo_dias: int,
    ease_factor: float,
) -> dict[str, Any]:
    """Calcula o próximo estado SRS a partir da nota de auto-avaliação.

    Retorna dict com repeticoes, intervalo_dias, ease_factor, due_em (ISO) e lapso (bool).
    """
    q = _NOTA_TO_Q.get(int(nota), 4)
    ease = float(ease_factor or 2.5)

    # Atualização do ease factor (fórmula clássica do SM-2).
    ease = ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    if ease < _EASE_MIN:
        ease = _EASE_MIN

    lapso = False
    if q < 3:
        # Falhou: reinicia repetições e reagenda para daqui a poucos minutos.
        lapso = True
        repeticoes = 0
        intervalo_dias = 0
        due = _now() + timedelta(minutes=_AGAIN_MINUTES)
    else:
        if repeticoes <= 0:
            intervalo_dias = 1
        elif repeticoes == 1:
            intervalo_dias = 6
        else:
            intervalo_dias = max(1, round((intervalo_dias or 1) * ease))
        repeticoes += 1
        due = _now() + timedelta(days=intervalo_dias)

    return {
        "repeticoes": repeticoes,
        "intervalo_dias": intervalo_dias,
        "ease_factor": round(ease, 3),
        "due_em": _iso(due),
        "lapso": lapso,
    }


def _tags_load(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _card_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "deck_id": int(row["deck_id"]),
        "ordem": int(row["ordem"]),
        "frente": row["frente"],
        "verso": row["verso"],
        "dica": row["dica"],
        "tags": _tags_load(row["tags_json"]),
        "dificuldade": row["dificuldade"],
        "referencia": row["referencia"],
        "fonte": {
            "chunk_id": row["chunk_id"],
            "pagina_inicio": row["pagina_inicio"],
            "pagina_fim": row["pagina_fim"],
        },
        "repeticoes": int(row["repeticoes"]),
        "intervalo_dias": int(row["intervalo_dias"]),
        "ease_factor": float(row["ease_factor"]),
        "due_em": row["due_em"],
        "ultima_revisao_em": row["ultima_revisao_em"],
        "lapsos": int(row["lapsos"]),
        "total_revisoes": int(row["total_revisoes"]),
    }


def _insert_cards(conn, deck_id: int, cards: list[dict[str, Any]], *, start_ordem: int = 0) -> list[int]:
    ids: list[int] = []
    now = _iso(_now())
    for i, c in enumerate(cards, start=start_ordem):
        fonte = c.get("fonte") or {}
        if not isinstance(fonte, dict):
            fonte = {
                "chunk_id": getattr(fonte, "chunk_id", None),
                "pagina_inicio": getattr(fonte, "pagina_inicio", None),
                "pagina_fim": getattr(fonte, "pagina_fim", None),
            }
        cur = conn.execute(
            """INSERT INTO flashcards
               (deck_id, ordem, frente, verso, dica, tags_json, dificuldade,
                referencia, chunk_id, pagina_inicio, pagina_fim, due_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                deck_id,
                i,
                c.get("frente", ""),
                c.get("verso", ""),
                c.get("dica"),
                json.dumps(c.get("tags") or [], ensure_ascii=False),
                c.get("dificuldade"),
                c.get("referencia"),
                fonte.get("chunk_id"),
                fonte.get("pagina_inicio"),
                fonte.get("pagina_fim"),
                now,
            ),
        )
        ids.append(int(cur.lastrowid))
    return ids


def save_deck(
    *,
    titulo: str,
    cards: list[Flashcard] | list[dict[str, Any]],
    documento_id: Optional[int] = None,
    trilha_id: Optional[int] = None,
    etapa_id: Optional[int] = None,
    descricao: Optional[str] = None,
    tema: Optional[str] = None,
    idioma: Optional[str] = None,
    fonte: Optional[str] = None,
    modelo: Optional[str] = None,
    meta: Optional[dict] = None,
) -> int:
    init_db()
    cards_dicts: list[dict[str, Any]] = []
    for c in cards:
        if isinstance(c, Flashcard):
            cards_dicts.append(c.model_dump())
        elif isinstance(c, dict):
            cards_dicts.append(c)
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO flashcard_decks
               (documento_id, trilha_id, etapa_id, titulo, descricao, tema, idioma, fonte, modelo, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                documento_id,
                trilha_id,
                etapa_id,
                titulo,
                descricao,
                tema,
                idioma,
                fonte,
                modelo,
                json.dumps(meta, ensure_ascii=False, default=str) if meta else None,
            ),
        )
        deck_id = int(cur.lastrowid)
        _insert_cards(conn, deck_id, cards_dicts)
        conn.commit()
        return deck_id


def add_cards(deck_id: int, cards: list[dict[str, Any]]) -> list[int]:
    init_db()
    with connect() as conn:
        deck = conn.execute(
            "SELECT id FROM flashcard_decks WHERE id = ?", (deck_id,)
        ).fetchone()
        if not deck:
            raise KeyError(f"deck {deck_id} não encontrado")
        max_ordem = conn.execute(
            "SELECT COALESCE(MAX(ordem), -1) AS m FROM flashcards WHERE deck_id = ?",
            (deck_id,),
        ).fetchone()["m"]
        ids = _insert_cards(conn, deck_id, cards, start_ordem=int(max_ordem) + 1)
        conn.commit()
        return ids


def _deck_counts(conn, deck_id: int) -> tuple[int, int, int]:
    now = _iso(_now())
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM flashcards WHERE deck_id = ?", (deck_id,)
    ).fetchone()["c"]
    due = conn.execute(
        "SELECT COUNT(*) AS c FROM flashcards WHERE deck_id = ? AND due_em <= ?",
        (deck_id, now),
    ).fetchone()["c"]
    novos = conn.execute(
        "SELECT COUNT(*) AS c FROM flashcards WHERE deck_id = ? AND total_revisoes = 0",
        (deck_id,),
    ).fetchone()["c"]
    return int(total), int(due), int(novos)


def list_decks(*, documento_id: Optional[int] = None, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    wheres = []
    params: list[Any] = []
    if documento_id is not None:
        wheres.append("dk.documento_id = ?")
        params.append(documento_id)
    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT dk.*, d.nome_arquivo
                FROM flashcard_decks dk
                LEFT JOIN documentos d ON d.id = dk.documento_id
                {where_sql}
                ORDER BY dk.criado_em DESC
                LIMIT ?""",
            (*params, limit),
        ).fetchall()
        out = []
        for r in rows:
            total, due, novos = _deck_counts(conn, int(r["id"]))
            out.append(
                {
                    "id": int(r["id"]),
                    "documento_id": r["documento_id"],
                    "titulo": r["titulo"],
                    "descricao": r["descricao"],
                    "tema": r["tema"],
                    "idioma": r["idioma"],
                    "fonte": r["fonte"],
                    "modelo": r["modelo"],
                    "nome_arquivo": r["nome_arquivo"],
                    "criado_em": r["criado_em"],
                    "total_cards": total,
                    "cards_due": due,
                    "cards_novos": novos,
                }
            )
        return out


def get_deck(deck_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        r = conn.execute(
            """SELECT dk.*, d.nome_arquivo
               FROM flashcard_decks dk
               LEFT JOIN documentos d ON d.id = dk.documento_id
               WHERE dk.id = ?""",
            (deck_id,),
        ).fetchone()
        if not r:
            return None
        cards = conn.execute(
            "SELECT * FROM flashcards WHERE deck_id = ? ORDER BY ordem",
            (deck_id,),
        ).fetchall()
        total, due, novos = _deck_counts(conn, deck_id)
    meta = None
    if r["meta_json"]:
        try:
            meta = json.loads(r["meta_json"])
        except Exception:
            meta = None
    return {
        "id": int(r["id"]),
        "documento_id": r["documento_id"],
        "titulo": r["titulo"],
        "descricao": r["descricao"],
        "tema": r["tema"],
        "idioma": r["idioma"],
        "fonte": r["fonte"],
        "modelo": r["modelo"],
        "nome_arquivo": r["nome_arquivo"],
        "criado_em": r["criado_em"],
        "meta": meta,
        "total_cards": total,
        "cards_due": due,
        "cards_novos": novos,
        "cards": [_card_row_to_dict(c) for c in cards],
    }


def delete_deck(deck_id: int) -> bool:
    init_db()
    with connect() as conn:
        cur = conn.execute("DELETE FROM flashcard_decks WHERE id = ?", (deck_id,))
        conn.commit()
        return cur.rowcount > 0


def get_due_cards(
    *, deck_id: Optional[int] = None, limit: int = 20, incluir_novos: bool = True
) -> dict[str, Any]:
    init_db()
    now = _iso(_now())
    wheres = ["due_em <= ?"]
    params: list[Any] = [now]
    if deck_id is not None:
        wheres.append("deck_id = ?")
        params.append(deck_id)
    if not incluir_novos:
        wheres.append("total_revisoes > 0")
    where_sql = "WHERE " + " AND ".join(wheres)
    with connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM flashcards {where_sql}", tuple(params)
        ).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT * FROM flashcards {where_sql}
                ORDER BY (total_revisoes = 0) ASC, due_em ASC
                LIMIT ?""",
            (*params, limit),
        ).fetchall()
    return {
        "deck_id": deck_id,
        "total_due": int(total),
        "cards": [_card_row_to_dict(r) for r in rows],
    }


def registrar_revisao(
    *, flashcard_id: int, nota: int, tempo_resposta_ms: Optional[int] = None
) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM flashcards WHERE id = ?", (flashcard_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"flashcard {flashcard_id} não encontrado")

        prev_intervalo = int(row["intervalo_dias"])
        nxt = sm2_next(
            nota=nota,
            repeticoes=int(row["repeticoes"]),
            intervalo_dias=prev_intervalo,
            ease_factor=float(row["ease_factor"]),
        )
        now = _iso(_now())
        lapsos = int(row["lapsos"]) + (1 if nxt["lapso"] else 0)
        total_revisoes = int(row["total_revisoes"]) + 1

        conn.execute(
            """UPDATE flashcards
               SET repeticoes=?, intervalo_dias=?, ease_factor=?, due_em=?,
                   ultima_revisao_em=?, lapsos=?, total_revisoes=?
               WHERE id=?""",
            (
                nxt["repeticoes"],
                nxt["intervalo_dias"],
                nxt["ease_factor"],
                nxt["due_em"],
                now,
                lapsos,
                total_revisoes,
                flashcard_id,
            ),
        )
        conn.execute(
            """INSERT INTO flashcard_revisoes
               (flashcard_id, nota, intervalo_anterior, intervalo_novo, ease_factor, tempo_resposta_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                flashcard_id,
                int(nota),
                prev_intervalo,
                nxt["intervalo_dias"],
                nxt["ease_factor"],
                tempo_resposta_ms,
            ),
        )
        conn.commit()
    return {
        "flashcard_id": flashcard_id,
        "nota": int(nota),
        "intervalo_anterior_dias": prev_intervalo,
        "intervalo_novo_dias": nxt["intervalo_dias"],
        "repeticoes": nxt["repeticoes"],
        "ease_factor": nxt["ease_factor"],
        "due_em": nxt["due_em"],
        "lapsos": lapsos,
        "total_revisoes": total_revisoes,
    }


def update_card(card_id: int, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not updates:
        raise ValueError("nenhum campo para atualizar")
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM flashcards WHERE id = ?", (card_id,)).fetchone()
        if not row:
            return None
        sets: list[str] = []
        params: list[Any] = []
        field_map = {
            "frente": "frente",
            "verso": "verso",
            "dica": "dica",
            "dificuldade": "dificuldade",
            "referencia": "referencia",
        }
        for key, col in field_map.items():
            if key in updates:
                sets.append(f"{col}=?")
                params.append(updates[key])
        if "tags" in updates:
            sets.append("tags_json=?")
            params.append(json.dumps(updates["tags"] or [], ensure_ascii=False))
        if not sets:
            raise ValueError("nenhum campo para atualizar")
        params.append(card_id)
        conn.execute(f"UPDATE flashcards SET {', '.join(sets)} WHERE id=?", tuple(params))
        conn.commit()
        updated = conn.execute("SELECT * FROM flashcards WHERE id = ?", (card_id,)).fetchone()
    return _card_row_to_dict(updated) if updated else None


def delete_card(card_id: int) -> bool:
    init_db()
    with connect() as conn:
        cur = conn.execute("DELETE FROM flashcards WHERE id = ?", (card_id,))
        conn.commit()
        return cur.rowcount > 0


def _status_card(total_revisoes: int, intervalo_dias: int) -> str:
    """Classifica o card para a visão de progresso."""
    if total_revisoes <= 0:
        return "novo"
    if intervalo_dias >= 21:
        return "dominado"
    return "aprendendo"


def get_card_revisoes(card_id: int, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, nota, intervalo_anterior, intervalo_novo, ease_factor,
                      tempo_resposta_ms, criado_em
               FROM flashcard_revisoes
               WHERE flashcard_id = ?
               ORDER BY criado_em DESC
               LIMIT ?""",
            (card_id, limit),
        ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "nota": int(r["nota"]),
            "intervalo_anterior": r["intervalo_anterior"],
            "intervalo_novo": r["intervalo_novo"],
            "ease_factor": r["ease_factor"],
            "tempo_resposta_ms": r["tempo_resposta_ms"],
            "criado_em": r["criado_em"],
        }
        for r in rows
    ]


def get_deck_progresso(deck_id: int) -> Optional[dict[str, Any]]:
    """Visão 'o que já fiz': cada card com seu estado SRS + agregados de revisão."""
    init_db()
    with connect() as conn:
        deck = conn.execute(
            """SELECT dk.*, d.nome_arquivo
               FROM flashcard_decks dk
               LEFT JOIN documentos d ON d.id = dk.documento_id
               WHERE dk.id = ?""",
            (deck_id,),
        ).fetchone()
        if not deck:
            return None
        rows = conn.execute(
            """SELECT fc.*,
                      (SELECT COUNT(*) FROM flashcard_revisoes r WHERE r.flashcard_id = fc.id AND r.nota >= 2) AS acertos,
                      (SELECT COUNT(*) FROM flashcard_revisoes r WHERE r.flashcard_id = fc.id AND r.nota < 2) AS erros,
                      (SELECT nota FROM flashcard_revisoes r WHERE r.flashcard_id = fc.id ORDER BY r.criado_em DESC LIMIT 1) AS ultima_nota
               FROM flashcards fc
               WHERE fc.deck_id = ?
               ORDER BY fc.ordem""",
            (deck_id,),
        ).fetchall()
        total, due, novos = _deck_counts(conn, deck_id)

    cards = []
    revisados = 0
    dominados = 0
    soma_revisoes = 0
    for r in rows:
        base = _card_row_to_dict(r)
        status = _status_card(base["total_revisoes"], base["intervalo_dias"])
        if base["total_revisoes"] > 0:
            revisados += 1
        if status == "dominado":
            dominados += 1
        soma_revisoes += base["total_revisoes"]
        cards.append(
            {
                **base,
                "acertos": int(r["acertos"] or 0),
                "erros": int(r["erros"] or 0),
                "ultima_nota": None if r["ultima_nota"] is None else int(r["ultima_nota"]),
                "status": status,
            }
        )

    return {
        "id": int(deck["id"]),
        "titulo": deck["titulo"],
        "nome_arquivo": deck["nome_arquivo"],
        "tema": deck["tema"],
        "total_cards": total,
        "cards_due": due,
        "cards_novos": novos,
        "cards_revisados": revisados,
        "cards_dominados": dominados,
        "total_revisoes": soma_revisoes,
        "cards": cards,
    }


def _deck_ids_trilha(conn, trilha_id: int, documento_id: Optional[int]) -> list[int]:
    """Decks vinculados à trilha ou ao mesmo material."""
    wheres = ["trilha_id = ?"]
    params: list[Any] = [trilha_id]
    if documento_id is not None:
        wheres.append("documento_id = ?")
        params.append(documento_id)
    where_sql = " OR ".join(wheres)
    rows = conn.execute(
        f"SELECT id FROM flashcard_decks WHERE {where_sql}",
        tuple(params),
    ).fetchall()
    return list({int(r["id"]) for r in rows})


def count_cards_trilha(*, trilha_id: int, documento_id: Optional[int] = None) -> dict[str, int]:
    """Agrega due/novos/total dos baralhos da trilha."""
    init_db()
    now = _iso(_now())
    with connect() as conn:
        deck_ids = _deck_ids_trilha(conn, trilha_id, documento_id)
        if not deck_ids:
            return {"cards_total": 0, "cards_due": 0, "cards_novos": 0}
        placeholders = ",".join("?" * len(deck_ids))
        total = int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM flashcards WHERE deck_id IN ({placeholders})",
                deck_ids,
            ).fetchone()["c"]
        )
        due = int(
            conn.execute(
                f"""SELECT COUNT(*) AS c FROM flashcards
                    WHERE deck_id IN ({placeholders}) AND due_em <= ?""",
                (*deck_ids, now),
            ).fetchone()["c"]
        )
        novos = int(
            conn.execute(
                f"""SELECT COUNT(*) AS c FROM flashcards
                    WHERE deck_id IN ({placeholders}) AND total_revisoes = 0""",
                deck_ids,
            ).fetchone()["c"]
        )
    return {"cards_total": total, "cards_due": due, "cards_novos": novos}


def get_due_cards_trilha(
    *,
    trilha_id: int,
    documento_id: Optional[int] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Cards due/novos dos baralhos ligados à trilha."""
    init_db()
    now = _iso(_now())
    with connect() as conn:
        deck_ids = _deck_ids_trilha(conn, trilha_id, documento_id)
        if not deck_ids:
            return []
        placeholders = ",".join("?" * len(deck_ids))
        rows = conn.execute(
            f"""SELECT * FROM flashcards
                WHERE deck_id IN ({placeholders}) AND due_em <= ?
                ORDER BY (total_revisoes = 0) ASC, due_em ASC
                LIMIT ?""",
            (*deck_ids, now, limit),
        ).fetchall()
    return [_card_row_to_dict(r) for r in rows]


def vincular_deck_trilha(deck_id: int, trilha_id: int, etapa_id: Optional[int] = None) -> bool:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE flashcard_decks SET trilha_id = ?, etapa_id = ? WHERE id = ?",
            (trilha_id, etapa_id, deck_id),
        )
        conn.commit()
    return cur.rowcount > 0


def get_estatisticas() -> dict[str, Any]:
    init_db()
    now = _iso(_now())
    with connect() as conn:
        totais = conn.execute(
            """SELECT
                  (SELECT COUNT(*) FROM flashcard_decks) AS decks,
                  (SELECT COUNT(*) FROM flashcards) AS cards,
                  (SELECT COUNT(*) FROM flashcard_revisoes) AS revisoes"""
        ).fetchone()
        due = conn.execute(
            "SELECT COUNT(*) AS c FROM flashcards WHERE due_em <= ?", (now,)
        ).fetchone()["c"]
        novos = conn.execute(
            "SELECT COUNT(*) AS c FROM flashcards WHERE total_revisoes = 0"
        ).fetchone()["c"]
        # "Dominados": já revisados, intervalo >= 21 dias (estável na memória de longo prazo).
        dominados = conn.execute(
            "SELECT COUNT(*) AS c FROM flashcards WHERE total_revisoes > 0 AND intervalo_dias >= 21"
        ).fetchone()["c"]
        por_dificuldade = conn.execute(
            "SELECT COALESCE(dificuldade,'-') AS k, COUNT(*) AS c FROM flashcards GROUP BY k"
        ).fetchall()
        por_idioma = conn.execute(
            """SELECT COALESCE(dk.idioma,'-') AS k, COUNT(fc.id) AS c
               FROM flashcards fc LEFT JOIN flashcard_decks dk ON dk.id = fc.deck_id
               GROUP BY k"""
        ).fetchall()
    return {
        "decks": int(totais["decks"]),
        "cards": int(totais["cards"]),
        "revisoes": int(totais["revisoes"]),
        "cards_due_hoje": int(due),
        "cards_novos": int(novos),
        "cards_dominados": int(dominados),
        "por_dificuldade": {r["k"]: int(r["c"]) for r in por_dificuldade},
        "por_idioma": {r["k"]: int(r["c"]) for r in por_idioma},
    }
