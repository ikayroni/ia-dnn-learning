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


def get_documento_row(documento_id: int) -> Optional[dict[str, Any]]:
    """Retorna a linha crua do documento (sem juntar gerações)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM documentos WHERE id = ?", (documento_id,)
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
) -> tuple[int, list[int]]:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO geracoes
               (documento_id, tema, palavras_chave, pagina_inicio, pagina_fim,
                tipos, dificuldade, instrucoes_extras, num_questoes_por_chunk,
                max_chunks, idioma, estilo, num_alternativas, incluir_explicacao,
                modelo, meta_json, questoes_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                parametros.get("idioma"),
                parametros.get("estilo"),
                parametros.get("num_alternativas"),
                1 if parametros.get("incluir_explicacao") else 0,
                meta.get("modelo"),
                json.dumps(meta, ensure_ascii=False, default=str),
                len(questoes),
            ),
        )
        geracao_id = int(cur.lastrowid)
        questao_ids: list[int] = []

        for i, q in enumerate(questoes):
            fonte = q.fonte
            qcur = conn.execute(
                """INSERT INTO questoes
                   (geracao_id, ordem, tipo, enunciado, alternativas_json,
                    gabarito, dificuldade, chunk_id, pagina_inicio, pagina_fim,
                    explicacao, explicacoes_alternativas_json, referencia,
                    idioma, estilo)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    q.explicacao,
                    json.dumps(q.explicacoes_alternativas, ensure_ascii=False)
                    if q.explicacoes_alternativas
                    else None,
                    q.referencia,
                    q.idioma,
                    q.estilo,
                ),
            )
            questao_ids.append(int(qcur.lastrowid))
        conn.commit()
        return geracao_id, questao_ids


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
                      tipos, dificuldade, idioma, estilo, num_alternativas,
                      modelo, questoes_count, criado_em
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
    def _safe_load(row, key):
        try:
            return json.loads(row[key]) if row[key] else None
        except Exception:
            return None

    def _row_get(row, key, default=None):
        try:
            value = row[key]
        except (IndexError, KeyError):
            return default
        return value if value is not None else default

    ger["questoes"] = [
        {
            "id": int(q["id"]),
            "ordem": q["ordem"],
            "tipo": q["tipo"],
            "enunciado": q["enunciado"],
            "alternativas": _safe_load(q, "alternativas_json"),
            "gabarito": q["gabarito"],
            "dificuldade": q["dificuldade"],
            "fonte": {
                "chunk_id": q["chunk_id"],
                "pagina_inicio": q["pagina_inicio"],
                "pagina_fim": q["pagina_fim"],
            },
            "explicacao": _row_get(q, "explicacao"),
            "explicacoes_alternativas": _safe_load(q, "explicacoes_alternativas_json"),
            "referencia": _row_get(q, "referencia"),
            "idioma": _row_get(q, "idioma"),
            "estilo": _row_get(q, "estilo"),
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


def list_banco_questoes(
    *,
    documento_id: Optional[int] = None,
    geracao_id: Optional[int] = None,
    tipo: Optional[str] = None,
    dificuldade: Optional[str] = None,
    idioma: Optional[str] = None,
    estilo: Optional[str] = None,
    so_erradas: bool = False,
    so_nao_respondidas: bool = False,
    busca: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Lista questões do banco com filtros + estatísticas por questão.

    Retorno: {"total": N, "questoes": [...], "stats_gerais": {...}}
    """
    init_db()
    wheres = []
    params: list[Any] = []
    if documento_id is not None:
        wheres.append("g.documento_id = ?")
        params.append(documento_id)
    if geracao_id is not None:
        wheres.append("q.geracao_id = ?")
        params.append(geracao_id)
    if tipo:
        wheres.append("q.tipo = ?")
        params.append(tipo)
    if dificuldade:
        wheres.append("q.dificuldade = ?")
        params.append(dificuldade)
    if idioma:
        wheres.append("q.idioma = ?")
        params.append(idioma)
    if estilo:
        wheres.append("q.estilo = ?")
        params.append(estilo)
    if busca:
        wheres.append("q.enunciado LIKE ?")
        params.append(f"%{busca}%")

    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

    base_sql = f"""
        SELECT q.id, q.geracao_id, q.tipo, q.enunciado, q.alternativas_json,
               q.gabarito, q.dificuldade, q.chunk_id, q.pagina_inicio, q.pagina_fim,
               q.explicacao, q.explicacoes_alternativas_json, q.referencia,
               q.idioma, q.estilo,
               g.documento_id, g.tema, g.modelo, g.criado_em AS geracao_criada_em,
               d.nome_arquivo,
               (SELECT COUNT(*) FROM tentativas t WHERE t.questao_id = q.id) AS tentativas_count,
               (SELECT COUNT(*) FROM tentativas t WHERE t.questao_id = q.id AND t.acertou = 1) AS acertos,
               (SELECT MAX(criado_em) FROM tentativas t WHERE t.questao_id = q.id) AS ultima_tentativa
        FROM questoes q
        LEFT JOIN geracoes g ON g.id = q.geracao_id
        LEFT JOIN documentos d ON d.id = g.documento_id
        {where_sql}
    """

    with connect() as conn:
        rows = conn.execute(
            base_sql + " ORDER BY q.id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM questoes q LEFT JOIN geracoes g ON g.id = q.geracao_id {where_sql}",
            tuple(params),
        ).fetchone()["c"]

        stats = conn.execute(
            f"""SELECT
                  COUNT(DISTINCT q.id) AS questoes,
                  COALESCE(SUM(CASE WHEN t.id IS NOT NULL THEN 1 ELSE 0 END), 0) AS tentativas,
                  COALESCE(SUM(CASE WHEN t.acertou = 1 THEN 1 ELSE 0 END), 0) AS acertos
                FROM questoes q
                LEFT JOIN geracoes g ON g.id = q.geracao_id
                LEFT JOIN tentativas t ON t.questao_id = q.id
                {where_sql}""",
            tuple(params),
        ).fetchone()

    items = []
    for r in rows:
        try:
            alts = json.loads(r["alternativas_json"]) if r["alternativas_json"] else None
        except Exception:
            alts = None
        try:
            expl_alts = (
                json.loads(r["explicacoes_alternativas_json"])
                if r["explicacoes_alternativas_json"]
                else None
            )
        except Exception:
            expl_alts = None
        tentativas_count = int(r["tentativas_count"] or 0)
        acertos = int(r["acertos"] or 0)
        item = {
            "id": int(r["id"]),
            "geracao_id": r["geracao_id"],
            "documento_id": r["documento_id"],
            "nome_arquivo": r["nome_arquivo"],
            "tipo": r["tipo"],
            "enunciado": r["enunciado"],
            "alternativas": alts,
            "gabarito": r["gabarito"],
            "dificuldade": r["dificuldade"],
            "fonte": {
                "chunk_id": r["chunk_id"],
                "pagina_inicio": r["pagina_inicio"],
                "pagina_fim": r["pagina_fim"],
            },
            "explicacao": r["explicacao"],
            "explicacoes_alternativas": expl_alts,
            "referencia": r["referencia"],
            "idioma": r["idioma"],
            "estilo": r["estilo"],
            "tema": r["tema"],
            "modelo": r["modelo"],
            "geracao_criada_em": r["geracao_criada_em"],
            "tentativas_count": tentativas_count,
            "acertos": acertos,
            "erros": max(0, tentativas_count - acertos),
            "ultima_tentativa": r["ultima_tentativa"],
            "ja_respondida": tentativas_count > 0,
            "ja_acertou": acertos > 0,
        }
        items.append(item)

    if so_nao_respondidas:
        items = [q for q in items if not q["ja_respondida"]]
    if so_erradas:
        items = [
            q
            for q in items
            if q["tentativas_count"] > 0 and q["erros"] > 0
        ]

    return {
        "total": int(total),
        "questoes": items,
        "stats_gerais": {
            "questoes": int(stats["questoes"]),
            "tentativas": int(stats["tentativas"]),
            "acertos": int(stats["acertos"]),
            "erros": int(stats["tentativas"]) - int(stats["acertos"]),
            "taxa_acerto": (
                round(int(stats["acertos"]) / int(stats["tentativas"]), 3)
                if int(stats["tentativas"]) > 0
                else None
            ),
        },
    }


def _row_to_questao_dict(row: Any) -> dict[str, Any]:
    try:
        alts = json.loads(row["alternativas_json"]) if row["alternativas_json"] else None
    except Exception:
        alts = None
    try:
        expl_alts = (
            json.loads(row["explicacoes_alternativas_json"])
            if row["explicacoes_alternativas_json"]
            else None
        )
    except Exception:
        expl_alts = None
    return {
        "id": int(row["id"]),
        "geracao_id": row["geracao_id"],
        "tipo": row["tipo"],
        "enunciado": row["enunciado"],
        "alternativas": alts,
        "gabarito": row["gabarito"],
        "dificuldade": row["dificuldade"],
        "fonte": {
            "chunk_id": row["chunk_id"],
            "pagina_inicio": row["pagina_inicio"],
            "pagina_fim": row["pagina_fim"],
        },
        "explicacao": row["explicacao"],
        "explicacoes_alternativas": expl_alts,
        "referencia": row["referencia"],
        "idioma": row["idioma"],
        "estilo": row["estilo"],
    }


def _validar_gabarito_alternativas(
    gabarito: str,
    alternativas: Optional[list[str]],
    tipo: str,
) -> None:
    if tipo != "multipla_escolha" or not alternativas:
        return
    g = gabarito.strip().upper()
    if len(g) != 1 or not g.isalpha():
        raise ValueError("gabarito deve ser uma letra (A, B, C, …) em múltipla escolha")
    idx = ord(g) - ord("A")
    if idx < 0 or idx >= len(alternativas):
        raise ValueError(
            f"gabarito '{gabarito}' fora do intervalo das alternativas (A–{chr(64 + len(alternativas))})"
        )


def update_questao(questao_id: int, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Atualiza campos da questão. Retorna o registro atualizado ou None se não existir."""
    if not updates:
        raise ValueError("nenhum campo para atualizar")
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM questoes WHERE id = ?", (questao_id,)).fetchone()
        if not row:
            return None

        tipo = updates.get("tipo", row["tipo"])
        enunciado = updates.get("enunciado", row["enunciado"])
        if not str(enunciado).strip():
            raise ValueError("enunciado não pode ser vazio")

        if "alternativas" in updates:
            alts = updates["alternativas"]
            alts_json = (
                json.dumps(alts, ensure_ascii=False) if alts is not None else None
            )
        else:
            try:
                alts = (
                    json.loads(row["alternativas_json"])
                    if row["alternativas_json"]
                    else None
                )
            except Exception:
                alts = None
            alts_json = row["alternativas_json"]

        gabarito = updates.get("gabarito", row["gabarito"])
        _validar_gabarito_alternativas(str(gabarito), alts, tipo)

        sets: list[str] = []
        params: list[Any] = []

        field_map = {
            "tipo": "tipo",
            "enunciado": "enunciado",
            "gabarito": "gabarito",
            "dificuldade": "dificuldade",
            "explicacao": "explicacao",
            "referencia": "referencia",
            "idioma": "idioma",
            "estilo": "estilo",
        }
        for key, col in field_map.items():
            if key in updates:
                sets.append(f"{col}=?")
                params.append(updates[key])

        if "alternativas" in updates:
            sets.append("alternativas_json=?")
            params.append(alts_json)

        if "explicacoes_alternativas" in updates:
            expl = updates["explicacoes_alternativas"]
            sets.append("explicacoes_alternativas_json=?")
            params.append(
                json.dumps(expl, ensure_ascii=False) if expl is not None else None
            )

        fonte = updates.get("fonte")
        if fonte is not None:
            if isinstance(fonte, dict):
                chunk_id = fonte.get("chunk_id")
                pagina_inicio = fonte.get("pagina_inicio")
                pagina_fim = fonte.get("pagina_fim")
            else:
                chunk_id = getattr(fonte, "chunk_id", None)
                pagina_inicio = getattr(fonte, "pagina_inicio", None)
                pagina_fim = getattr(fonte, "pagina_fim", None)
            sets.extend(["chunk_id=?", "pagina_inicio=?", "pagina_fim=?"])
            params.extend([chunk_id, pagina_inicio, pagina_fim])

        if not sets:
            raise ValueError("nenhum campo para atualizar")

        params.append(questao_id)
        conn.execute(
            f"UPDATE questoes SET {', '.join(sets)} WHERE id=?",
            tuple(params),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM questoes WHERE id = ?", (questao_id,)
        ).fetchone()
    return _row_to_questao_dict(updated) if updated else None


def get_questao_with_tentativas(questao_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """SELECT q.*, g.documento_id, g.tema, g.modelo, g.criado_em AS geracao_criada_em,
                      d.nome_arquivo
               FROM questoes q
               LEFT JOIN geracoes g ON g.id = q.geracao_id
               LEFT JOIN documentos d ON d.id = g.documento_id
               WHERE q.id = ?""",
            (questao_id,),
        ).fetchone()
        if not row:
            return None
        tentativas = conn.execute(
            "SELECT * FROM tentativas WHERE questao_id = ? ORDER BY criado_em DESC",
            (questao_id,),
        ).fetchall()
    base = _row_to_questao_dict(row)
    base.update(
        {
            "documento_id": row["documento_id"],
            "nome_arquivo": row["nome_arquivo"],
            "tema": row["tema"],
            "modelo": row["modelo"],
            "geracao_criada_em": row["geracao_criada_em"],
        }
    )
    return {
        **base,
        "tentativas": [
            {
                "id": int(t["id"]),
                "resposta_usuario": t["resposta_usuario"],
                "acertou": bool(t["acertou"]),
                "tempo_resposta_ms": t["tempo_resposta_ms"],
                "comentario": t["comentario"],
                "dificuldade_percebida": t["dificuldade_percebida"] if "dificuldade_percebida" in t.keys() else None,
                "criado_em": t["criado_em"],
            }
            for t in tentativas
        ],
    }


def registrar_tentativa(
    *,
    questao_id: int,
    resposta_usuario: str,
    tempo_resposta_ms: Optional[int] = None,
    comentario: Optional[str] = None,
    dificuldade_percebida: Optional[str] = None,
) -> dict[str, Any]:
    """Registra a resposta do usuário. Compara com o gabarito da questão e marca acertou/errou.

    Retorna: {"tentativa_id": int, "acertou": bool, "gabarito": "B", "explicacao": "..."}.
    """
    init_db()
    resposta = (resposta_usuario or "").strip()
    with connect() as conn:
        q = conn.execute(
            "SELECT id, gabarito, explicacao, explicacoes_alternativas_json FROM questoes WHERE id = ?",
            (questao_id,),
        ).fetchone()
        if not q:
            raise KeyError(f"questao_id {questao_id} não encontrada")
        gabarito = str(q["gabarito"]).strip()
        acertou = _comparar_resposta(resposta, gabarito)
        cur = conn.execute(
            """INSERT INTO tentativas
               (questao_id, resposta_usuario, acertou, tempo_resposta_ms, comentario, dificuldade_percebida)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (questao_id, resposta, 1 if acertou else 0, tempo_resposta_ms, comentario, dificuldade_percebida),
        )
        conn.commit()
        tentativa_id = int(cur.lastrowid)
        try:
            expl_alts = (
                json.loads(q["explicacoes_alternativas_json"])
                if q["explicacoes_alternativas_json"]
                else None
            )
        except Exception:
            expl_alts = None
        return {
            "tentativa_id": tentativa_id,
            "acertou": acertou,
            "gabarito": gabarito,
            "explicacao": q["explicacao"],
            "explicacoes_alternativas": expl_alts,
        }


def atualizar_tentativa_feedback(
    tentativa_id: int,
    *,
    dificuldade_percebida: str,
) -> None:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE tentativas SET dificuldade_percebida = ? WHERE id = ?",
            (dificuldade_percebida, tentativa_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"tentativa_id {tentativa_id} não encontrada")


def _comparar_resposta(resposta: str, gabarito: str) -> bool:
    r = resposta.strip().upper()
    g = gabarito.strip().upper()
    if r == g:
        return True
    # tolera "B)" ou "B." ou "b - texto"
    if len(g) == 1 and r.startswith(g) and (len(r) == 1 or not r[1].isalnum()):
        return True
    # Verdadeiro/Falso em diferentes idiomas
    sinonimos = {
        "V": {"VERDADEIRO", "TRUE", "VERO", "T"},
        "F": {"FALSO", "FALSE", "F"},
    }
    for letra, alts in sinonimos.items():
        if g.startswith(letra) and r in alts:
            return True
    return False


def get_banco_estatisticas() -> dict[str, Any]:
    """Resumo geral do banco: totais por dificuldade, idioma, estilo + ranking de erradas."""
    init_db()
    with connect() as conn:
        totais = conn.execute(
            """SELECT
                  (SELECT COUNT(*) FROM documentos) AS documentos,
                  (SELECT COUNT(*) FROM geracoes) AS geracoes,
                  (SELECT COUNT(*) FROM questoes) AS questoes,
                  (SELECT COUNT(*) FROM tentativas) AS tentativas,
                  (SELECT COUNT(*) FROM tentativas WHERE acertou=1) AS acertos"""
        ).fetchone()
        por_dificuldade = conn.execute(
            "SELECT COALESCE(dificuldade,'-') AS k, COUNT(*) AS c FROM questoes GROUP BY k"
        ).fetchall()
        por_idioma = conn.execute(
            "SELECT COALESCE(idioma,'-') AS k, COUNT(*) AS c FROM questoes GROUP BY k"
        ).fetchall()
        por_estilo = conn.execute(
            "SELECT COALESCE(estilo,'-') AS k, COUNT(*) AS c FROM questoes GROUP BY k"
        ).fetchall()
        mais_erradas = conn.execute(
            """SELECT q.id, q.enunciado, q.gabarito,
                      COUNT(t.id) AS tentativas,
                      SUM(CASE WHEN t.acertou=0 THEN 1 ELSE 0 END) AS erros
               FROM tentativas t
               JOIN questoes q ON q.id = t.questao_id
               GROUP BY q.id
               HAVING erros > 0
               ORDER BY erros DESC, tentativas DESC
               LIMIT 10"""
        ).fetchall()
    tentativas = int(totais["tentativas"])
    acertos = int(totais["acertos"])
    return {
        "documentos": int(totais["documentos"]),
        "geracoes": int(totais["geracoes"]),
        "questoes": int(totais["questoes"]),
        "tentativas": tentativas,
        "acertos": acertos,
        "erros": tentativas - acertos,
        "taxa_acerto": round(acertos / tentativas, 3) if tentativas else None,
        "por_dificuldade": {r["k"]: int(r["c"]) for r in por_dificuldade},
        "por_idioma": {r["k"]: int(r["c"]) for r in por_idioma},
        "por_estilo": {r["k"]: int(r["c"]) for r in por_estilo},
        "mais_erradas": [
            {
                "id": int(r["id"]),
                "enunciado": r["enunciado"],
                "gabarito": r["gabarito"],
                "tentativas": int(r["tentativas"]),
                "erros": int(r["erros"] or 0),
            }
            for r in mais_erradas
        ],
    }


def export_geracao_csv(geracao_id: int) -> Optional[str]:
    import csv
    import io

    ger = get_geracao_with_questoes(geracao_id)
    if not ger:
        return None
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "ordem",
            "tipo",
            "idioma",
            "estilo",
            "enunciado",
            "alt_A",
            "alt_B",
            "alt_C",
            "alt_D",
            "alt_E",
            "alt_F",
            "gabarito",
            "dificuldade",
            "pagina",
            "explicacao",
            "expl_A",
            "expl_B",
            "expl_C",
            "expl_D",
            "expl_E",
            "expl_F",
            "referencia",
        ]
    )
    for q in ger["questoes"]:
        alts = list(q.get("alternativas") or [])
        alts = (alts + [""] * 6)[:6]
        expl_alts = q.get("explicacoes_alternativas") or {}
        writer.writerow(
            [
                q["ordem"] + 1,
                q["tipo"],
                q.get("idioma") or "",
                q.get("estilo") or "",
                q["enunciado"],
                *alts,
                q["gabarito"],
                q["dificuldade"] or "",
                q["fonte"]["pagina_inicio"] or "",
                q.get("explicacao") or "",
                expl_alts.get("A", ""),
                expl_alts.get("B", ""),
                expl_alts.get("C", ""),
                expl_alts.get("D", ""),
                expl_alts.get("E", ""),
                expl_alts.get("F", ""),
                q.get("referencia") or "",
            ]
        )
    return buf.getvalue()
