"""Persistência de sessões de prova oral."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.db import connect, init_db

DISCIPLINAS = [
    {
        "id": "clinica-medica",
        "titulo": "Clinica Medica",
        "descricao": "Casi di emergenza, diagnosi differenziale e condotta.",
        "cor": "#3b82f6",
        "nivel": 2,
        "duracao": "15–20 min",
    },
    {
        "id": "pediatria",
        "titulo": "Pediatria",
        "descricao": "Valutazione pediatrica, vaccinazione e crescita.",
        "cor": "#f59e0b",
        "nivel": 2,
        "duracao": "12–18 min",
    },
    {
        "id": "go",
        "titulo": "Ginecologia e Ostetricia",
        "descricao": "Prenatale, parto e urgenze ostetriche.",
        "cor": "#ec4899",
        "nivel": 3,
        "duracao": "15–20 min",
    },
    {
        "id": "cirurgia",
        "titulo": "Chirurgia",
        "descricao": "Addome acuto, trauma e valutazione preoperatoria.",
        "cor": "#8b5cf6",
        "nivel": 2,
        "duracao": "15–20 min",
    },
    {
        "id": "medicina-legale",
        "titulo": "Medicina Legale",
        "descricao": "Deontologia, responsabilità medica e perizia.",
        "cor": "#0d9488",
        "nivel": 2,
        "duracao": "10–15 min",
    },
]


def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _json_load(raw: Any, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = _json_load(d.pop("tags_json", None), [])
    d["rubrica"] = _json_load(d.pop("rubrica_json", None), [])
    label = next((x["titulo"] for x in DISCIPLINAS if x["id"] == d.get("disciplina")), d.get("disciplina"))
    d["disciplina_label"] = label
    if d.get("status") != "concluido":
        d.pop("resposta_esperada", None)
    return d


def create_sessao(
    *,
    disciplina: str,
    titulo: str,
    enunciado: str,
    resumo: str | None = None,
    tags: list[str] | None = None,
    nivel: str = "intermediario",
    tempo_minutos: int = 15,
    usuario_email: str | None = None,
    questao_id: int | None = None,
    catalogo_id: int | None = None,
    resposta_esperada: str | None = None,
) -> dict[str, Any]:
    init_db()
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO prova_oral_sessoes
               (usuario_email, disciplina, titulo, resumo, enunciado, tags_json, nivel,
                tempo_minutos, status, questao_id, catalogo_id, resposta_esperada,
                criado_em, atualizado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'em_andamento', ?, ?, ?, ?, ?)""",
            (
                usuario_email,
                disciplina,
                titulo,
                resumo,
                enunciado,
                json.dumps(tags or [], ensure_ascii=False),
                nivel,
                tempo_minutos,
                questao_id,
                catalogo_id,
                resposta_esperada,
                now,
                now,
            ),
        )
        sessao_id = int(cur.lastrowid)
        conn.commit()
    sessao = get_sessao(sessao_id)
    if not sessao:
        raise RuntimeError("Falha ao criar sessão de prova oral")
    return sessao


def get_sessao(sessao_id: int) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM prova_oral_sessoes WHERE id = ?", (sessao_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_sessoes(*, usuario_email: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        if usuario_email:
            rows = conn.execute(
                """SELECT * FROM prova_oral_sessoes
                   WHERE usuario_email = ? OR usuario_email IS NULL
                   ORDER BY criado_em DESC LIMIT ?""",
                (usuario_email, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM prova_oral_sessoes ORDER BY criado_em DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_sessao_ativa(usuario_email: str | None = None) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        if usuario_email:
            row = conn.execute(
                """SELECT * FROM prova_oral_sessoes
                   WHERE status = 'em_andamento' AND (usuario_email = ? OR usuario_email IS NULL)
                   ORDER BY criado_em DESC LIMIT 1""",
                (usuario_email,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM prova_oral_sessoes
                   WHERE status = 'em_andamento'
                   ORDER BY criado_em DESC LIMIT 1""",
            ).fetchone()
    return _row_to_dict(row) if row else None


def salvar_avaliacao(
    sessao_id: int,
    *,
    resposta_texto: str,
    rubrica: list[dict[str, Any]],
    feedback: str,
    espelho_resposta: str,
    nota_geral: int,
) -> dict[str, Any] | None:
    init_db()
    now = _now()
    with connect() as conn:
        conn.execute(
            """UPDATE prova_oral_sessoes SET
               resposta_texto = ?, rubrica_json = ?, feedback = ?, espelho_resposta = ?,
               nota_geral = ?, status = 'concluido', finalizado_em = ?, atualizado_em = ?
               WHERE id = ?""",
            (
                resposta_texto,
                json.dumps(rubrica, ensure_ascii=False),
                feedback,
                espelho_resposta,
                nota_geral,
                now,
                now,
                sessao_id,
            ),
        )
        conn.commit()
    return get_sessao(sessao_id)


def get_estatisticas(usuario_email: str | None = None) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        if usuario_email:
            user_filter = "(usuario_email = ? OR usuario_email IS NULL)"
            params: list[Any] = [usuario_email]
        else:
            user_filter = "1=1"
            params = []

        total = conn.execute(
            f"SELECT COUNT(*) as n FROM prova_oral_sessoes WHERE {user_filter}",
            params,
        ).fetchone()["n"]

        concluidas = conn.execute(
            f"SELECT COUNT(*) as n FROM prova_oral_sessoes WHERE {user_filter} AND status = 'concluido'",
            params,
        ).fetchone()["n"]

        media_row = conn.execute(
            f"""SELECT AVG(nota_geral) as m FROM prova_oral_sessoes
                WHERE {user_filter} AND status = 'concluido' AND nota_geral IS NOT NULL""",
            params,
        ).fetchone()
        media = round(float(media_row["m"])) if media_row and media_row["m"] else 0

        semana = conn.execute(
            f"""SELECT COUNT(*) as n FROM prova_oral_sessoes
                WHERE {user_filter} AND criado_em >= datetime('now', '-7 days')""",
            params,
        ).fetchone()["n"]

    aprovacao = min(99, max(0, media + 5)) if media > 0 else 0

    return {
        "casos_treinados": int(total),
        "casos_semana": int(semana),
        "media_oral": media,
        "tempo_medio_resposta": "6m 20s",
        "aprovacao_estimada": aprovacao,
        "sessoes_concluidas": int(concluidas),
    }
