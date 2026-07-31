"""Catálogo de provas orais pré-prontas (perguntas + respostas modelo)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.db import connect, init_db

_RESOURCES = Path(__file__).resolve().parent / "resources"
_SEED_FILES = [
    _RESOURCES / "prova_oral_medicina_legale.json",
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


def _catalogo_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["total_questoes"] = d.pop("total_questoes", 0) or 0
    return d


def _questao_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = _json_load(d.pop("tags_json", None), [])
    return d


def ensure_seed_catalogos() -> None:
    """Carrega JSONs de seed se o catálogo ainda não existir."""
    init_db()
    for path in _SEED_FILES:
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        slug = str(data.get("slug", "")).strip()
        if not slug:
            continue
        with connect() as conn:
            exists = conn.execute(
                "SELECT id FROM prova_oral_catalogos WHERE slug = ?",
                (slug,),
            ).fetchone()
            if exists:
                continue
            cur = conn.execute(
                """INSERT INTO prova_oral_catalogos
                   (slug, titulo, disciplina, descricao, fonte, tempo_minutos, ativo, criado_em)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    slug,
                    data.get("titulo", slug),
                    data.get("disciplina", "medicina-legale"),
                    data.get("descricao"),
                    data.get("fonte"),
                    int(data.get("tempo_minutos") or 10),
                    _now(),
                ),
            )
            catalogo_id = int(cur.lastrowid)
            for q in data.get("questoes") or []:
                conn.execute(
                    """INSERT INTO prova_oral_questoes
                       (catalogo_id, ordem, pergunta, resposta_esperada, tags_json, tempo_minutos, ativo, criado_em)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                    (
                        catalogo_id,
                        int(q.get("ordem") or 0),
                        str(q.get("pergunta", "")),
                        str(q.get("resposta_esperada", "")),
                        json.dumps(q.get("tags") or [], ensure_ascii=False),
                        q.get("tempo_minutos"),
                        _now(),
                    ),
                )
            conn.commit()


def list_catalogos(*, disciplina: str | None = None, ativo_only: bool = True) -> list[dict[str, Any]]:
    ensure_seed_catalogos()
    init_db()
    clauses = []
    params: list[Any] = []
    if ativo_only:
        clauses.append("c.ativo = 1")
    if disciplina:
        clauses.append("c.disciplina = ?")
        params.append(disciplina)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT c.*,
                       (SELECT COUNT(*) FROM prova_oral_questoes q
                        WHERE q.catalogo_id = c.id AND q.ativo = 1) AS total_questoes
                FROM prova_oral_catalogos c
                {where}
                ORDER BY c.disciplina, c.titulo""",
            params,
        ).fetchall()
    return [_catalogo_row(r) for r in rows]


def get_catalogo(catalogo_id: int) -> dict[str, Any] | None:
    ensure_seed_catalogos()
    init_db()
    with connect() as conn:
        row = conn.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM prova_oral_questoes q
                       WHERE q.catalogo_id = c.id AND q.ativo = 1) AS total_questoes
               FROM prova_oral_catalogos c WHERE c.id = ?""",
            (catalogo_id,),
        ).fetchone()
    return _catalogo_row(row) if row else None


def list_questoes(catalogo_id: int, *, ativo_only: bool = True) -> list[dict[str, Any]]:
    ensure_seed_catalogos()
    init_db()
    clause = "AND ativo = 1" if ativo_only else ""
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT * FROM prova_oral_questoes
                WHERE catalogo_id = ? {clause}
                ORDER BY ordem, id""",
            (catalogo_id,),
        ).fetchall()
    return [_questao_row(r) for r in rows]


def get_questao(questao_id: int) -> dict[str, Any] | None:
    ensure_seed_catalogos()
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM prova_oral_questoes WHERE id = ?",
            (questao_id,),
        ).fetchone()
    return _questao_row(row) if row else None


def pick_questao_aleatoria(catalogo_id: int) -> dict[str, Any] | None:
    ensure_seed_catalogos()
    init_db()
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM prova_oral_questoes
               WHERE catalogo_id = ? AND ativo = 1
               ORDER BY RANDOM() LIMIT 1""",
            (catalogo_id,),
        ).fetchone()
    return _questao_row(row) if row else None


def create_catalogo(
    *,
    slug: str,
    titulo: str,
    disciplina: str,
    descricao: str | None = None,
    fonte: str | None = None,
    tempo_minutos: int = 10,
) -> dict[str, Any]:
    init_db()
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO prova_oral_catalogos
               (slug, titulo, disciplina, descricao, fonte, tempo_minutos, ativo, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (slug, titulo, disciplina, descricao, fonte, tempo_minutos, now),
        )
        catalogo_id = int(cur.lastrowid)
        conn.commit()
    cat = get_catalogo(catalogo_id)
    if not cat:
        raise RuntimeError("Falha ao criar catálogo de prova oral")
    return cat


def create_questao(
    catalogo_id: int,
    *,
    pergunta: str,
    resposta_esperada: str,
    ordem: int = 0,
    tags: list[str] | None = None,
    tempo_minutos: int | None = None,
) -> dict[str, Any]:
    init_db()
    if not get_catalogo(catalogo_id):
        raise ValueError("Catálogo não encontrado")
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO prova_oral_questoes
               (catalogo_id, ordem, pergunta, resposta_esperada, tags_json, tempo_minutos, ativo, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                catalogo_id,
                ordem,
                pergunta,
                resposta_esperada,
                json.dumps(tags or [], ensure_ascii=False),
                tempo_minutos,
                now,
            ),
        )
        questao_id = int(cur.lastrowid)
        conn.commit()
    q = get_questao(questao_id)
    if not q:
        raise RuntimeError("Falha ao criar questão de prova oral")
    return q
