"""Pastas de organização para flashcards e mapas mentais."""

from __future__ import annotations

from typing import Any, Optional

from app.db import connect, init_db

_VALID_TIPOS = frozenset({"flashcards", "mapas"})
_ITEM_TABLE = {
    "flashcards": "flashcard_decks",
    "mapas": "mapas_mentais",
}


def _count_items(conn, tipo: str, pasta_id: int) -> int:
    table = _ITEM_TABLE[tipo]
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE pasta_id = ?",
        (pasta_id,),
    ).fetchone()
    return int(row["c"] if row else 0)


def _pasta_row(conn, row: Any, tipo: str) -> dict[str, Any]:
    pasta_id = int(row["id"])
    return {
        "id": pasta_id,
        "tipo": row["tipo"],
        "nome": row["nome"],
        "descricao": row["descricao"],
        "ordem": int(row["ordem"] or 0),
        "criado_em": row["criado_em"],
        "total_itens": _count_items(conn, tipo, pasta_id),
    }


def list_pastas(*, tipo: str) -> list[dict[str, Any]]:
    if tipo not in _VALID_TIPOS:
        raise ValueError("tipo inválido")
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM study_pastas
               WHERE tipo = ?
               ORDER BY ordem ASC, nome COLLATE NOCASE ASC""",
            (tipo,),
        ).fetchall()
        return [_pasta_row(conn, r, tipo) for r in rows]


def get_pasta(pasta_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM study_pastas WHERE id = ?", (pasta_id,)).fetchone()
        if not row:
            return None
        return _pasta_row(conn, row, row["tipo"])


def create_pasta(*, tipo: str, nome: str, descricao: Optional[str] = None, ordem: Optional[int] = None) -> int:
    if tipo not in _VALID_TIPOS:
        raise ValueError("tipo inválido")
    init_db()
    with connect() as conn:
        if ordem is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(ordem), -1) + 1 AS next_ordem FROM study_pastas WHERE tipo = ?",
                (tipo,),
            ).fetchone()
            ordem = int(row["next_ordem"] if row else 0)
        cur = conn.execute(
            """INSERT INTO study_pastas (tipo, nome, descricao, ordem)
               VALUES (?, ?, ?, ?)""",
            (tipo, (nome or "").strip(), (descricao or None), int(ordem)),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_pasta(pasta_id: int, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    init_db()
    field_map = {"nome": "nome", "descricao": "descricao", "ordem": "ordem"}
    sets: list[str] = []
    params: list[Any] = []
    for key, col in field_map.items():
        if key in updates:
            sets.append(f"{col}=?")
            params.append(updates[key])
    if not sets:
        return get_pasta(pasta_id)
    with connect() as conn:
        if conn.execute("SELECT id FROM study_pastas WHERE id = ?", (pasta_id,)).fetchone() is None:
            return None
        params.append(pasta_id)
        conn.execute(f"UPDATE study_pastas SET {', '.join(sets)} WHERE id = ?", tuple(params))
        conn.commit()
    return get_pasta(pasta_id)


def delete_pasta(pasta_id: int) -> bool:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT tipo FROM study_pastas WHERE id = ?", (pasta_id,)).fetchone()
        if not row:
            return False
        table = _ITEM_TABLE[row["tipo"]]
        conn.execute(f"UPDATE {table} SET pasta_id = NULL WHERE pasta_id = ?", (pasta_id,))
        cur = conn.execute("DELETE FROM study_pastas WHERE id = ?", (pasta_id,))
        conn.commit()
        return cur.rowcount > 0
