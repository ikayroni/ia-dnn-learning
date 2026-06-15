"""Persistência de trilhas de estudo, etapas, salas e atividades."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from app.db import connect, init_db


def _json_loads(val: Optional[str], default: Any = None) -> Any:
    if not val:
        return default
    try:
        return json.loads(val)
    except Exception:
        return default


def _row_etapa(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["palavras_chave"] = _json_loads(d.get("palavras_chave"), [])
    return d


def _row_sala(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["meta"] = _json_loads(d.get("meta_json"))
    d.pop("meta_json", None)
    return d


def _row_atividade(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["payload"] = _json_loads(d.get("payload_json"), {})
    d.pop("payload_json", None)
    return d


def _row_trilha(row: Any, *, etapas: Optional[list] = None, salas: Optional[list] = None) -> dict[str, Any]:
    d = dict(row)
    d["plano"] = _json_loads(d.get("plano_json"), {})
    d.pop("plano_json", None)
    d["meta"] = _json_loads(d.get("meta_json"))
    d.pop("meta_json", None)
    if etapas is not None:
        d["etapas"] = etapas
    if salas is not None:
        d["salas"] = salas
    return d


def _insert_etapa(conn, trilha_id: int, e: dict, ordem: int) -> int:
    cur = conn.execute(
        """INSERT INTO trilha_etapas
           (trilha_id, ordem, modulo, titulo, objetivo, conteudo,
            pagina_inicio, pagina_fim, tema, palavras_chave, duracao_minutos)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trilha_id,
            ordem,
            e.get("modulo"),
            e.get("titulo") or f"Etapa {ordem}",
            e.get("objetivo"),
            e.get("conteudo"),
            e.get("pagina_inicio"),
            e.get("pagina_fim"),
            e.get("tema"),
            json.dumps(e.get("palavras_chave") or [], ensure_ascii=False),
            e.get("duracao_minutos"),
        ),
    )
    return int(cur.lastrowid)


def create_trilha(
    *,
    documento_id: Optional[int] = None,
    titulo: str,
    objetivo: Optional[str],
    horas_por_dia: Optional[float],
    semanas: Optional[int],
    plano: Optional[dict] = None,
    meta: Optional[dict],
    etapas: list[dict],
    origem: str = "ia",
) -> int:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO trilhas
               (documento_id, titulo, objetivo, horas_por_dia, semanas,
                etapa_atual, plano_json, meta_json, origem)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                documento_id,
                titulo,
                objetivo,
                horas_por_dia,
                semanas,
                json.dumps(plano or {}, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False) if meta else None,
                origem,
            ),
        )
        trilha_id = int(cur.lastrowid)
        for i, e in enumerate(etapas, start=1):
            _insert_etapa(conn, trilha_id, e, e.get("ordem") or i)
        conn.commit()
    return trilha_id


_TRILHA_EDITAVEL = {"titulo", "objetivo", "horas_por_dia", "semanas", "status", "documento_id"}


def update_trilha(trilha_id: int, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    init_db()
    campos = {k: v for k, v in updates.items() if k in _TRILHA_EDITAVEL}
    with connect() as conn:
        existe = conn.execute("SELECT id FROM trilhas WHERE id = ?", (trilha_id,)).fetchone()
        if not existe:
            return None
        if campos:
            sets = ", ".join(f"{k} = ?" for k in campos)
            params = list(campos.values()) + [trilha_id]
            conn.execute(
                f"UPDATE trilhas SET {sets}, atualizado_em = datetime('now') WHERE id = ?",
                params,
            )
            conn.commit()
    return get_trilha(trilha_id)


_ETAPA_EDITAVEL = {
    "modulo",
    "titulo",
    "objetivo",
    "conteudo",
    "pagina_inicio",
    "pagina_fim",
    "tema",
    "duracao_minutos",
    "status",
}


def create_etapa(trilha_id: int, etapa: dict[str, Any]) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        tr = conn.execute("SELECT id FROM trilhas WHERE id = ?", (trilha_id,)).fetchone()
        if not tr:
            return None
        row = conn.execute(
            "SELECT COALESCE(MAX(ordem), 0) AS m FROM trilha_etapas WHERE trilha_id = ?",
            (trilha_id,),
        ).fetchone()
        ordem = int(row["m"]) + 1
        etapa_id = _insert_etapa(conn, trilha_id, etapa, ordem)
        conn.execute(
            "UPDATE trilhas SET atualizado_em = datetime('now') WHERE id = ?", (trilha_id,)
        )
        conn.commit()
    return get_etapa(etapa_id)


def update_etapa(etapa_id: int, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Edita conteúdo da etapa (campos textuais + palavras_chave)."""
    init_db()
    campos: dict[str, Any] = {k: v for k, v in updates.items() if k in _ETAPA_EDITAVEL}
    if "palavras_chave" in updates:
        kws = updates["palavras_chave"]
        if isinstance(kws, str):
            kws = [k.strip() for k in kws.split(",") if k.strip()]
        campos["palavras_chave"] = json.dumps(kws or [], ensure_ascii=False)
    with connect() as conn:
        row = conn.execute(
            "SELECT trilha_id FROM trilha_etapas WHERE id = ?", (etapa_id,)
        ).fetchone()
        if not row:
            return None
        if campos:
            sets = ", ".join(f"{k} = ?" for k in campos)
            extra = ""
            if "status" in campos:
                extra = (
                    ", concluida_em = datetime('now')"
                    if campos["status"] == "concluida"
                    else ", concluida_em = NULL"
                )
            conn.execute(
                f"UPDATE trilha_etapas SET {sets}{extra} WHERE id = ?",
                list(campos.values()) + [etapa_id],
            )
            conn.execute(
                "UPDATE trilhas SET atualizado_em = datetime('now') WHERE id = ?",
                (row["trilha_id"],),
            )
            conn.commit()
    return get_etapa(etapa_id)


def delete_etapa(etapa_id: int) -> bool:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT trilha_id FROM trilha_etapas WHERE id = ?", (etapa_id,)
        ).fetchone()
        if not row:
            return False
        trilha_id = int(row["trilha_id"])
        conn.execute("DELETE FROM trilha_etapas WHERE id = ?", (etapa_id,))
        # Reordena as etapas restantes para manter a sequência 1..N
        restantes = conn.execute(
            "SELECT id FROM trilha_etapas WHERE trilha_id = ? ORDER BY ordem",
            (trilha_id,),
        ).fetchall()
        for i, r in enumerate(restantes, start=1):
            conn.execute("UPDATE trilha_etapas SET ordem = ? WHERE id = ?", (i, r["id"]))
        # Garante etapa_atual válida
        total = len(restantes)
        if total == 0:
            conn.execute("UPDATE trilhas SET etapa_atual = 1 WHERE id = ?", (trilha_id,))
        else:
            conn.execute(
                "UPDATE trilhas SET etapa_atual = MIN(etapa_atual, ?) WHERE id = ?",
                (total, trilha_id),
            )
        conn.execute(
            "UPDATE trilhas SET atualizado_em = datetime('now') WHERE id = ?", (trilha_id,)
        )
        conn.commit()
    return True


def reorder_etapas(trilha_id: int, ordered_ids: list[int]) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        tr = conn.execute("SELECT id FROM trilhas WHERE id = ?", (trilha_id,)).fetchone()
        if not tr:
            return None
        atuais = {
            int(r["id"])
            for r in conn.execute(
                "SELECT id FROM trilha_etapas WHERE trilha_id = ?", (trilha_id,)
            ).fetchall()
        }
        # Aplica a nova ordem para os ids válidos, na sequência informada
        ordem = 0
        for eid in ordered_ids:
            if int(eid) in atuais:
                ordem += 1
                conn.execute(
                    "UPDATE trilha_etapas SET ordem = ? WHERE id = ? AND trilha_id = ?",
                    (ordem, int(eid), trilha_id),
                )
        conn.execute(
            "UPDATE trilhas SET atualizado_em = datetime('now') WHERE id = ?", (trilha_id,)
        )
        conn.commit()
    return get_trilha(trilha_id)


def list_trilhas(*, documento_id: Optional[int] = None, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    wheres: list[str] = []
    params: list[Any] = []
    if documento_id is not None:
        wheres.append("documento_id = ?")
        params.append(documento_id)
    where_sql = f"WHERE {' AND '.join(wheres)}" if wheres else ""
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT id, documento_id, titulo, objetivo, horas_por_dia, semanas,
                       etapa_atual, status, criado_em, atualizado_em,
                       (SELECT COUNT(*) FROM trilha_etapas e WHERE e.trilha_id = trilhas.id) AS total_etapas,
                       (SELECT COUNT(*) FROM trilha_etapas e WHERE e.trilha_id = trilhas.id AND e.status = 'concluida') AS etapas_concluidas
                FROM trilhas {where_sql}
                ORDER BY criado_em DESC LIMIT ?""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_trilha(trilha_id: int, *, include_etapas: bool = True) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM trilhas WHERE id = ?", (trilha_id,)).fetchone()
        if not row:
            return None
        etapas = []
        if include_etapas:
            etapas = [
                _row_etapa(r)
                for r in conn.execute(
                    "SELECT * FROM trilha_etapas WHERE trilha_id = ? ORDER BY ordem",
                    (trilha_id,),
                ).fetchall()
            ]
    return _row_trilha(row, etapas=etapas)


def delete_trilha(trilha_id: int) -> bool:
    init_db()
    with connect() as conn:
        cur = conn.execute("DELETE FROM trilhas WHERE id = ?", (trilha_id,))
        conn.commit()
    return cur.rowcount > 0


def get_etapa(etapa_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM trilha_etapas WHERE id = ?", (etapa_id,)
        ).fetchone()
    return _row_etapa(row) if row else None


def get_etapa_atual(trilha_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        tr = conn.execute(
            "SELECT etapa_atual FROM trilhas WHERE id = ?", (trilha_id,)
        ).fetchone()
        if not tr:
            return None
        row = conn.execute(
            """SELECT * FROM trilha_etapas
               WHERE trilha_id = ? AND ordem = ?""",
            (trilha_id, tr["etapa_atual"]),
        ).fetchone()
    return _row_etapa(row) if row else None


def update_etapa_status(etapa_id: int, status: str) -> Optional[dict[str, Any]]:
    init_db()
    concluida = "datetime('now')" if status == "concluida" else "NULL"
    with connect() as conn:
        conn.execute(
            f"""UPDATE trilha_etapas
                SET status = ?, concluida_em = {concluida}
                WHERE id = ?""",
            (status, etapa_id),
        )
        row = conn.execute(
            "SELECT * FROM trilha_etapas WHERE id = ?", (etapa_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE trilhas SET atualizado_em = datetime('now') WHERE id = ?",
                (row["trilha_id"],),
            )
        conn.commit()
    return _row_etapa(row) if row else None


def avancar_trilha(trilha_id: int) -> Optional[dict[str, Any]]:
    """Marca etapa atual como concluída e incrementa etapa_atual (se houver próxima)."""
    init_db()
    with connect() as conn:
        tr = conn.execute("SELECT * FROM trilhas WHERE id = ?", (trilha_id,)).fetchone()
        if not tr:
            return None
        ordem_atual = int(tr["etapa_atual"])
        conn.execute(
            """UPDATE trilha_etapas
               SET status = 'concluida', concluida_em = datetime('now')
               WHERE trilha_id = ? AND ordem = ?""",
            (trilha_id, ordem_atual),
        )
        prox = conn.execute(
            """SELECT ordem FROM trilha_etapas
               WHERE trilha_id = ? AND ordem > ?
               ORDER BY ordem LIMIT 1""",
            (trilha_id, ordem_atual),
        ).fetchone()
        novo_status = tr["status"]
        if prox:
            nova_ordem = int(prox["ordem"])
        else:
            nova_ordem = ordem_atual
            novo_status = "concluida"
        conn.execute(
            """UPDATE trilhas
               SET etapa_atual = ?, status = ?, atualizado_em = datetime('now')
               WHERE id = ?""",
            (nova_ordem, novo_status, trilha_id),
        )
        conn.commit()
    return get_trilha(trilha_id)


def create_sala(
    *,
    trilha_id: int,
    etapa_id: Optional[int],
    dia_numero: Optional[int],
    titulo: str,
    resumo: Optional[str],
    meta: Optional[dict],
    atividades: list[dict],
) -> int:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO salas
               (trilha_id, etapa_id, dia_numero, titulo, resumo, meta_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                trilha_id,
                etapa_id,
                dia_numero,
                titulo,
                resumo,
                json.dumps(meta, ensure_ascii=False) if meta else None,
            ),
        )
        sala_id = int(cur.lastrowid)
        for i, a in enumerate(atividades, start=1):
            conn.execute(
                """INSERT INTO sala_atividades
                   (sala_id, ordem, tipo, titulo, descricao, duracao_minutos, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    sala_id,
                    a.get("ordem", i),
                    a["tipo"],
                    a["titulo"],
                    a.get("descricao"),
                    a.get("duracao_minutos"),
                    json.dumps(a.get("payload") or {}, ensure_ascii=False),
                ),
            )
        conn.execute(
            "UPDATE trilhas SET atualizado_em = datetime('now') WHERE id = ?",
            (trilha_id,),
        )
        conn.commit()
    return sala_id


def get_sala(sala_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM salas WHERE id = ?", (sala_id,)).fetchone()
        if not row:
            return None
        atividades = [
            _row_atividade(r)
            for r in conn.execute(
                "SELECT * FROM sala_atividades WHERE sala_id = ? ORDER BY ordem",
                (sala_id,),
            ).fetchall()
        ]
    sala = _row_sala(row)
    sala["atividades"] = atividades
    return sala


def list_salas_trilha(trilha_id: int, limit: int = 30) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM sala_atividades a WHERE a.sala_id = s.id) AS total_atividades,
                      (SELECT COUNT(*) FROM sala_atividades a WHERE a.sala_id = s.id AND a.status = 'concluida') AS atividades_concluidas
               FROM salas s
               WHERE s.trilha_id = ?
               ORDER BY s.criado_em DESC LIMIT ?""",
            (trilha_id, limit),
        ).fetchall()
    return [_row_sala(r) for r in rows]


def get_sala_aberta_etapa(trilha_id: int, etapa_id: int) -> Optional[dict[str, Any]]:
    """Sala aberta mais recente ligada à etapa."""
    init_db()
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM salas
               WHERE trilha_id = ? AND etapa_id = ? AND status = 'aberta'
               ORDER BY criado_em DESC LIMIT 1""",
            (trilha_id, etapa_id),
        ).fetchone()
    if not row:
        return None
    return get_sala(int(row["id"]))


def get_sala_hoje(trilha_id: int) -> Optional[dict[str, Any]]:
    """Sala criada hoje (data local do servidor) para a trilha."""
    init_db()
    hoje = date.today().isoformat()
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM salas
               WHERE trilha_id = ? AND date(criado_em) = ?
               ORDER BY criado_em DESC LIMIT 1""",
            (trilha_id, hoje),
        ).fetchone()
    if not row:
        return None
    return get_sala(int(row["id"]))


def update_atividade_status(atividade_id: int, status: str) -> Optional[dict[str, Any]]:
    init_db()
    valid = {"pendente", "concluida", "ignorada"}
    if status not in valid:
        raise ValueError(f"status deve ser um de: {valid}")
    concluida = "datetime('now')" if status in ("concluida", "ignorada") else "NULL"
    with connect() as conn:
        conn.execute(
            f"""UPDATE sala_atividades
                SET status = ?, concluida_em = {concluida}
                WHERE id = ?""",
            (status, atividade_id),
        )
        row = conn.execute(
            "SELECT * FROM sala_atividades WHERE id = ?", (atividade_id,)
        ).fetchone()
        if row:
            sala_id = int(row["sala_id"])
            pend = conn.execute(
                """SELECT COUNT(*) AS c FROM sala_atividades
                   WHERE sala_id = ? AND status = 'pendente'""",
                (sala_id,),
            ).fetchone()
            if pend and int(pend["c"]) == 0:
                conn.execute(
                    """UPDATE salas SET status = 'concluida', concluida_em = datetime('now')
                       WHERE id = ?""",
                    (sala_id,),
                )
        conn.commit()
    return _row_atividade(row) if row else None


def get_desempenho_documento(documento_id: int, limit: int = 8) -> dict[str, Any]:
    """Temas com mais erros e taxa geral — alimenta a sala adaptativa."""
    init_db()
    with connect() as conn:
        totais = conn.execute(
            """SELECT COUNT(t.id) AS tentativas,
                      SUM(CASE WHEN t.acertou=1 THEN 1 ELSE 0 END) AS acertos
               FROM tentativas t
               JOIN questoes q ON q.id = t.questao_id
               JOIN geracoes g ON g.id = q.geracao_id
               WHERE g.documento_id = ?""",
            (documento_id,),
        ).fetchone()
        fracassos = conn.execute(
            """SELECT COALESCE(g.tema, '-') AS tema,
                      COUNT(t.id) AS tentativas,
                      SUM(CASE WHEN t.acertou=0 THEN 1 ELSE 0 END) AS erros
               FROM tentativas t
               JOIN questoes q ON q.id = t.questao_id
               JOIN geracoes g ON g.id = q.geracao_id
               WHERE g.documento_id = ?
               GROUP BY tema
               HAVING erros > 0
               ORDER BY erros DESC, tentativas DESC
               LIMIT ?""",
            (documento_id, limit),
        ).fetchall()
    tentativas = int(totais["tentativas"] or 0)
    acertos = int(totais["acertos"] or 0)
    return {
        "tentativas": tentativas,
        "acertos": acertos,
        "taxa_acerto": round(acertos / tentativas, 3) if tentativas else None,
        "temas_fracos": [
            {"tema": r["tema"], "tentativas": int(r["tentativas"]), "erros": int(r["erros"] or 0)}
            for r in fracassos
        ],
    }
