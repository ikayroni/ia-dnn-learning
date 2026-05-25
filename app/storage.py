"""Persistência de documentos, gerações e questões."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from app.db import connect, init_db
from app.schemas import Questao


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_documento_by_hash(hash_sha256: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM documentos WHERE hash_sha256 = ?", (hash_sha256,)
        ).fetchone()
    return dict(row) if row else None


def get_documento_by_job(ocr_job_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM documentos WHERE ocr_job_id = ?", (ocr_job_id,)
        ).fetchone()
    return dict(row) if row else None


def upsert_documento(
    *,
    nome_arquivo: str,
    hash_sha256: Optional[str],
    paginas: Optional[int],
    caracteres: Optional[int],
    ocr_job_id: Optional[str],
    fonte: str,
) -> int:
    init_db()
    with connect() as conn:
        if hash_sha256:
            row = conn.execute(
                "SELECT id FROM documentos WHERE hash_sha256 = ?", (hash_sha256,)
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE documentos
                       SET nome_arquivo=COALESCE(?, nome_arquivo),
                           paginas=COALESCE(?, paginas),
                           caracteres=COALESCE(?, caracteres),
                           ocr_job_id=COALESCE(?, ocr_job_id),
                           fonte=COALESCE(?, fonte)
                       WHERE id=?""",
                    (nome_arquivo, paginas, caracteres, ocr_job_id, fonte, row["id"]),
                )
                conn.commit()
                return int(row["id"])
        cur = conn.execute(
            """INSERT INTO documentos
               (nome_arquivo, hash_sha256, paginas, caracteres, ocr_job_id, fonte)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (nome_arquivo, hash_sha256, paginas, caracteres, ocr_job_id, fonte),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_documento_ocr_done(
    *,
    documento_id: int,
    paginas: int,
    caracteres: int,
    ocr_job_id: str,
) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE documentos SET paginas=?, caracteres=?, ocr_job_id=? WHERE id=?",
            (paginas, caracteres, ocr_job_id, documento_id),
        )
        conn.commit()


def save_geracao(
    *,
    documento_id: Optional[int],
    questoes: list[Questao],
    meta: dict,
    parametros: dict,
) -> int:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO geracoes
               (documento_id, tema, palavras_chave, pagina_inicio, pagina_fim,
                tipos, dificuldade, instrucoes_extras, num_questoes_por_chunk,
                max_chunks, modelo, meta_json, questoes_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                documento_id,
                parametros.get("tema"),
                json.dumps(parametros.get("palavras_chave"), ensure_ascii=False)
                if parametros.get("palavras_chave")
                else None,
                parametros.get("pagina_inicio"),
                parametros.get("pagina_fim"),
                json.dumps(parametros.get("tipos"), ensure_ascii=False)
                if parametros.get("tipos")
                else None,
                parametros.get("dificuldade"),
                parametros.get("instrucoes_extras"),
                parametros.get("num_questoes_por_chunk"),
                parametros.get("max_chunks"),
                meta.get("modelo"),
                json.dumps(meta, ensure_ascii=False, default=str),
                len(questoes),
            ),
        )
        geracao_id = int(cur.lastrowid)

        for i, q in enumerate(questoes):
            fonte = q.fonte
            conn.execute(
                """INSERT INTO questoes
                   (geracao_id, ordem, tipo, enunciado, alternativas_json,
                    gabarito, dificuldade, chunk_id, pagina_inicio, pagina_fim)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    geracao_id,
                    i,
                    q.tipo,
                    q.enunciado,
                    json.dumps(q.alternativas, ensure_ascii=False) if q.alternativas else None,
                    q.gabarito,
                    q.dificuldade,
                    fonte.chunk_id if fonte else None,
                    fonte.pagina_inicio if fonte else None,
                    fonte.pagina_fim if fonte else None,
                ),
            )
        conn.commit()
        return geracao_id


def list_documentos(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.*,
                      (SELECT COUNT(*) FROM geracoes g WHERE g.documento_id = d.id) AS geracoes_count,
                      (SELECT COALESCE(SUM(questoes_count), 0) FROM geracoes g WHERE g.documento_id = d.id) AS questoes_total
               FROM documentos d
               ORDER BY d.criado_em DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_documento(documento_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM documentos WHERE id = ?", (documento_id,)
        ).fetchone()
        if not row:
            return None
        doc = dict(row)
        gers = conn.execute(
            """SELECT id, tema, palavras_chave, pagina_inicio, pagina_fim,
                      tipos, dificuldade, modelo, questoes_count, criado_em
               FROM geracoes WHERE documento_id = ? ORDER BY criado_em DESC""",
            (documento_id,),
        ).fetchall()
    doc["geracoes"] = [dict(g) for g in gers]
    for g in doc["geracoes"]:
        for k in ("palavras_chave", "tipos"):
            if g.get(k):
                try:
                    g[k] = json.loads(g[k])
                except Exception:
                    pass
    return doc


def get_geracao_with_questoes(geracao_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM geracoes WHERE id = ?", (geracao_id,)).fetchone()
        if not row:
            return None
        ger = dict(row)
        qs = conn.execute(
            "SELECT * FROM questoes WHERE geracao_id = ? ORDER BY ordem",
            (geracao_id,),
        ).fetchall()
    for k in ("palavras_chave", "tipos"):
        if ger.get(k):
            try:
                ger[k] = json.loads(ger[k])
            except Exception:
                pass
    if ger.get("meta_json"):
        try:
            ger["meta"] = json.loads(ger["meta_json"])
        except Exception:
            ger["meta"] = None
    ger["questoes"] = [
        {
            "ordem": q["ordem"],
            "tipo": q["tipo"],
            "enunciado": q["enunciado"],
            "alternativas": json.loads(q["alternativas_json"]) if q["alternativas_json"] else None,
            "gabarito": q["gabarito"],
            "dificuldade": q["dificuldade"],
            "fonte": {
                "chunk_id": q["chunk_id"],
                "pagina_inicio": q["pagina_inicio"],
                "pagina_fim": q["pagina_fim"],
            },
        }
        for q in qs
    ]
    return ger


def delete_documento(documento_id: int) -> bool:
    init_db()
    with connect() as conn:
        cur = conn.execute("DELETE FROM documentos WHERE id = ?", (documento_id,))
        conn.commit()
        return cur.rowcount > 0


def export_geracao_csv(geracao_id: int) -> Optional[str]:
    import csv
    import io

    ger = get_geracao_with_questoes(geracao_id)
    if not ger:
        return None
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        ["ordem", "tipo", "enunciado", "alt_A", "alt_B", "alt_C", "alt_D", "gabarito", "dificuldade", "pagina"]
    )
    for q in ger["questoes"]:
        alts = q["alternativas"] or []
        alts = alts + [""] * (4 - len(alts)) if len(alts) < 4 else alts[:4]
        writer.writerow(
            [
                q["ordem"] + 1,
                q["tipo"],
                q["enunciado"],
                alts[0],
                alts[1],
                alts[2],
                alts[3],
                q["gabarito"],
                q["dificuldade"] or "",
                q["fonte"]["pagina_inicio"] or "",
            ]
        )
    return buf.getvalue()
