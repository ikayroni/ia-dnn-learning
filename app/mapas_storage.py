"""Persistência de mapas mentais: mapa + nós (árvore com parent_id).

Espelha o padrão dos flashcards (`flashcards_storage.py`): SQL cru sobre SQLite,
`meta` serializado em coluna JSON. A árvore vem/entra como estrutura aninhada e é
achatada em linhas (`mapa_nos`) na escrita e remontada na leitura.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.db import connect, init_db


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Escrita da árvore
# ---------------------------------------------------------------------------


def _insert_no(
    conn,
    *,
    mapa_id: int,
    parent_id: Optional[int],
    ordem: int,
    titulo: str,
    nota: Optional[str],
    cor: Optional[str],
    colapsado: bool = False,
) -> int:
    cur = conn.execute(
        """INSERT INTO mapa_nos
           (mapa_id, parent_id, ordem, titulo, nota, cor, colapsado)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            mapa_id,
            parent_id,
            ordem,
            (titulo or "").strip() or "Sem título",
            (nota or None),
            (cor or None),
            1 if colapsado else 0,
        ),
    )
    return int(cur.lastrowid)


def _insert_tree(conn, *, mapa_id: int, node: dict[str, Any], parent_id: Optional[int], ordem: int) -> int:
    """Insere um nó e, recursivamente, seus filhos. Retorna o id do nó criado."""
    no_id = _insert_no(
        conn,
        mapa_id=mapa_id,
        parent_id=parent_id,
        ordem=ordem,
        titulo=str(node.get("titulo") or "").strip(),
        nota=(node.get("nota") or None),
        cor=(node.get("cor") or None),
    )
    filhos = node.get("filhos")
    if isinstance(filhos, list):
        for i, filho in enumerate(filhos):
            if isinstance(filho, dict):
                _insert_tree(conn, mapa_id=mapa_id, node=filho, parent_id=no_id, ordem=i)
    return no_id


def save_mapa(
    *,
    titulo: str,
    raiz: dict[str, Any],
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
    """Cria um mapa mental completo a partir de uma árvore aninhada (raiz)."""
    init_db()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO mapas_mentais
               (documento_id, trilha_id, etapa_id, titulo, descricao, tema, idioma, fonte, modelo, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                documento_id,
                trilha_id,
                etapa_id,
                (titulo or "Mapa mental").strip(),
                descricao,
                tema,
                idioma,
                fonte,
                modelo,
                json.dumps(meta, ensure_ascii=False, default=str) if meta else None,
            ),
        )
        mapa_id = int(cur.lastrowid)
        _insert_tree(conn, mapa_id=mapa_id, node=raiz, parent_id=None, ordem=0)
        conn.commit()
        return mapa_id


# ---------------------------------------------------------------------------
# Leitura / remontagem da árvore
# ---------------------------------------------------------------------------


def _build_tree(nodes: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    children: dict[Optional[int], list[dict[str, Any]]] = {}
    for r in nodes:
        node = {
            "id": int(r["id"]),
            "titulo": r["titulo"],
            "nota": r["nota"],
            "cor": r["cor"],
            "colapsado": bool(r["colapsado"]),
            "ordem": int(r["ordem"]),
            "pos_x": r["pos_x"] if "pos_x" in r else None,
            "pos_y": r["pos_y"] if "pos_y" in r else None,
            "imagem_url": r["imagem_url"] if "imagem_url" in r else None,
            "filhos": [],
        }
        by_id[node["id"]] = node
        parent = r["parent_id"]
        parent = int(parent) if parent is not None else None
        children.setdefault(parent, []).append(node)

    def attach(node: dict[str, Any]) -> None:
        filhos = sorted(children.get(node["id"], []), key=lambda n: (n["ordem"], n["id"]))
        node["filhos"] = filhos
        for f in filhos:
            attach(f)

    roots = sorted(children.get(None, []), key=lambda n: (n["ordem"], n["id"]))
    if not roots:
        return None
    raiz = roots[0]
    attach(raiz)
    return raiz


def _mapa_row(conn, mapa_id: int):
    return conn.execute(
        """SELECT m.*, d.nome_arquivo
           FROM mapas_mentais m
           LEFT JOIN documentos d ON d.id = m.documento_id
           WHERE m.id = ?""",
        (mapa_id,),
    ).fetchone()


def _count_nos(conn, mapa_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM mapa_nos WHERE mapa_id = ?", (mapa_id,)
        ).fetchone()["c"]
    )


def get_mapa(mapa_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        r = _mapa_row(conn, mapa_id)
        if not r:
            return None
        nodes = conn.execute(
            "SELECT * FROM mapa_nos WHERE mapa_id = ? ORDER BY ordem, id", (mapa_id,)
        ).fetchall()
        total = _count_nos(conn, mapa_id)
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
        "atualizado_em": r["atualizado_em"],
        "total_nos": total,
        "meta": meta,
        "raiz": _build_tree([dict(n) for n in nodes]),
    }


def list_mapas(*, documento_id: Optional[int] = None, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    wheres = []
    params: list[Any] = []
    if documento_id is not None:
        wheres.append("m.documento_id = ?")
        params.append(documento_id)
    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT m.*, d.nome_arquivo,
                       (SELECT COUNT(*) FROM mapa_nos n WHERE n.mapa_id = m.id) AS total_nos
                FROM mapas_mentais m
                LEFT JOIN documentos d ON d.id = m.documento_id
                {where_sql}
                ORDER BY m.criado_em DESC
                LIMIT ?""",
            (*params, limit),
        ).fetchall()
    return [
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
            "atualizado_em": r["atualizado_em"],
            "total_nos": int(r["total_nos"] or 0),
        }
        for r in rows
    ]


def delete_mapa(mapa_id: int) -> bool:
    init_db()
    with connect() as conn:
        cur = conn.execute("DELETE FROM mapas_mentais WHERE id = ?", (mapa_id,))
        conn.commit()
        return cur.rowcount > 0


def _touch(conn, mapa_id: int) -> None:
    conn.execute(
        "UPDATE mapas_mentais SET atualizado_em = ? WHERE id = ?",
        (_now_iso(), mapa_id),
    )


def update_mapa(mapa_id: int, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    init_db()
    field_map = {"titulo": "titulo", "descricao": "descricao", "tema": "tema"}
    sets: list[str] = []
    params: list[Any] = []
    for key, col in field_map.items():
        if key in updates and updates[key] is not None:
            sets.append(f"{col}=?")
            params.append(updates[key])
    with connect() as conn:
        if conn.execute("SELECT id FROM mapas_mentais WHERE id = ?", (mapa_id,)).fetchone() is None:
            return None
        if sets:
            params.append(mapa_id)
            conn.execute(f"UPDATE mapas_mentais SET {', '.join(sets)} WHERE id=?", tuple(params))
        _touch(conn, mapa_id)
        conn.commit()
    return get_mapa(mapa_id)


# ---------------------------------------------------------------------------
# CRUD de nós
# ---------------------------------------------------------------------------


def add_no(
    mapa_id: int,
    *,
    parent_id: Optional[int],
    titulo: str,
    nota: Optional[str] = None,
    cor: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        if conn.execute("SELECT id FROM mapas_mentais WHERE id = ?", (mapa_id,)).fetchone() is None:
            return None
        if parent_id is not None:
            parent = conn.execute(
                "SELECT id FROM mapa_nos WHERE id = ? AND mapa_id = ?",
                (parent_id, mapa_id),
            ).fetchone()
            if parent is None:
                raise KeyError(f"nó pai {parent_id} não encontrado no mapa {mapa_id}")
        prox = conn.execute(
            "SELECT COALESCE(MAX(ordem), -1) + 1 AS o FROM mapa_nos WHERE mapa_id = ? AND parent_id IS ?",
            (mapa_id, parent_id),
        ).fetchone()["o"]
        _insert_no(
            conn,
            mapa_id=mapa_id,
            parent_id=parent_id,
            ordem=int(prox),
            titulo=titulo,
            nota=nota,
            cor=cor,
        )
        _touch(conn, mapa_id)
        conn.commit()
    return get_mapa(mapa_id)


def _descendentes(conn, no_id: int) -> set[int]:
    """IDs de todos os descendentes de um nó (para evitar mover para dentro de si)."""
    out: set[int] = set()
    fila = [no_id]
    while fila:
        atual = fila.pop()
        filhos = conn.execute(
            "SELECT id FROM mapa_nos WHERE parent_id = ?", (atual,)
        ).fetchall()
        for f in filhos:
            fid = int(f["id"])
            if fid not in out:
                out.add(fid)
                fila.append(fid)
    return out


def update_no(no_id: int, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM mapa_nos WHERE id = ?", (no_id,)).fetchone()
        if not row:
            return None
        mapa_id = int(row["mapa_id"])
        sets: list[str] = []
        params: list[Any] = []
        if "titulo" in updates and updates["titulo"] is not None:
            sets.append("titulo=?")
            params.append(str(updates["titulo"]).strip() or "Sem título")
        if "nota" in updates:
            sets.append("nota=?")
            params.append(updates["nota"] or None)
        if "cor" in updates:
            sets.append("cor=?")
            params.append(updates["cor"] or None)
        if "colapsado" in updates and updates["colapsado"] is not None:
            sets.append("colapsado=?")
            params.append(1 if updates["colapsado"] else 0)
        if "ordem" in updates and updates["ordem"] is not None:
            sets.append("ordem=?")
            params.append(int(updates["ordem"]))
        if "pos_x" in updates:
            sets.append("pos_x=?")
            params.append(updates["pos_x"])
        if "pos_y" in updates:
            sets.append("pos_y=?")
            params.append(updates["pos_y"])
        if "imagem_url" in updates:
            sets.append("imagem_url=?")
            params.append(updates["imagem_url"] or None)
        if "parent_id" in updates and updates["parent_id"] is not None:
            novo_pai = int(updates["parent_id"])
            if row["parent_id"] is None:
                raise ValueError("Não é possível mover o nó raiz.")
            if novo_pai == no_id or novo_pai in _descendentes(conn, no_id):
                raise ValueError("Não é possível mover um nó para dentro de si mesmo.")
            pai = conn.execute(
                "SELECT id FROM mapa_nos WHERE id = ? AND mapa_id = ?",
                (novo_pai, mapa_id),
            ).fetchone()
            if pai is None:
                raise KeyError(f"nó pai {novo_pai} não encontrado no mapa {mapa_id}")
            sets.append("parent_id=?")
            params.append(novo_pai)
        if sets:
            params.append(no_id)
            conn.execute(f"UPDATE mapa_nos SET {', '.join(sets)} WHERE id=?", tuple(params))
        _touch(conn, mapa_id)
        conn.commit()
    return get_mapa(mapa_id)


def delete_no(no_id: int) -> Optional[dict[str, Any]]:
    """Remove um nó e seus descendentes (cascade). A raiz não pode ser removida."""
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM mapa_nos WHERE id = ?", (no_id,)).fetchone()
        if not row:
            return None
        if row["parent_id"] is None:
            raise ValueError("Não é possível excluir o nó raiz do mapa.")
        mapa_id = int(row["mapa_id"])
        conn.execute("DELETE FROM mapa_nos WHERE id = ?", (no_id,))
        _touch(conn, mapa_id)
        conn.commit()
    return get_mapa(mapa_id)
