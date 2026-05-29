"""Orquestração: gerar trilha e salas com Bedrock + SQLite."""

from __future__ import annotations

from typing import Any, Optional

from app.bedrock import generate_sala_dia, generate_trilha_plano
from app.config import effective_bedrock_model_id
from app.document_text import load_document_for_id
from app.generator import discover_topics_from_document
from app.trilha_storage import (
    avancar_trilha,
    create_sala,
    create_trilha,
    get_desempenho_documento,
    get_etapa_atual,
    get_sala,
    get_sala_aberta_etapa,
    get_sala_hoje,
    get_trilha,
)


def _sample_text(text: str, max_len: int = 10000) -> str:
    if len(text) <= max_len:
        return text
    third = max_len // 3
    return text[:third] + "\n\n" + text[len(text) // 2 : len(text) // 2 + third] + "\n\n" + text[-third:]


def _normalize_etapas(plano_etapas: list[dict], dias_estudo: int) -> list[dict]:
    out: list[dict] = []
    for i, e in enumerate(plano_etapas[:dias_estudo], start=1):
        kws = e.get("palavras_chave") or []
        if isinstance(kws, str):
            kws = [k.strip() for k in kws.split(",") if k.strip()]
        out.append(
            {
                "ordem": i,
                "modulo": e.get("modulo"),
                "titulo": e.get("titulo") or f"Dia {i}",
                "objetivo": e.get("objetivo"),
                "pagina_inicio": e.get("pagina_inicio"),
                "pagina_fim": e.get("pagina_fim"),
                "tema": e.get("tema"),
                "palavras_chave": kws,
                "duracao_minutos": e.get("duracao_minutos"),
            }
        )
    return out


def gerar_trilha(
    *,
    documento_id: int,
    objetivo: str = "Revalida / Residência Médica",
    semanas: int = 2,
    horas_por_dia: float = 1.0,
    dias_por_semana: int = 5,
    max_temas: int = 12,
    instrucoes_extras: Optional[str] = None,
    ocr_job_id: Optional[str] = None,
    texto: Optional[str] = None,
) -> dict[str, Any]:
    if semanas < 1 or semanas > 52:
        raise ValueError("semanas deve estar entre 1 e 52")
    if not (0.25 <= horas_por_dia <= 12):
        raise ValueError("horas_por_dia deve estar entre 0.25 e 12")
    if dias_por_semana < 1 or dias_por_semana > 7:
        raise ValueError("dias_por_semana deve estar entre 1 e 7")

    doc, row, fonte_texto = load_document_for_id(
        documento_id, ocr_job_id=ocr_job_id, texto=texto
    )
    dias_estudo = semanas * dias_por_semana

    temas_info = discover_topics_from_document(doc, max_topics=max_temas)
    temas = temas_info.get("temas") or []

    sample = _sample_text(doc.text)
    plano_llm = generate_trilha_plano(
        sample_text=sample,
        paginas=doc.page_count,
        temas=temas,
        objetivo=objetivo,
        semanas=semanas,
        horas_por_dia=horas_por_dia,
        dias_estudo=dias_estudo,
        instrucoes_extras=instrucoes_extras,
    )

    titulo = plano_llm.get("titulo") or f"Trilha — {objetivo[:60]}"
    etapas_raw = plano_llm.get("etapas") or []
    etapas = _normalize_etapas(etapas_raw, dias_estudo)
    if not etapas:
        raise ValueError("Nenhuma etapa gerada para a trilha.")

    meta = {
        "modelo": effective_bedrock_model_id(),
        "objetivo": objetivo,
        "semanas": semanas,
        "horas_por_dia": horas_por_dia,
        "dias_por_semana": dias_por_semana,
        "dias_estudo": dias_estudo,
        "temas_descobertos": temas,
        "resumo_plano": plano_llm.get("resumo"),
        "paginas_material": doc.page_count,
        "ocr_job_id": row.get("ocr_job_id"),
        "fonte_texto": fonte_texto,
        "caracteres_material": len(doc.text),
    }

    trilha_id = create_trilha(
        documento_id=documento_id,
        titulo=titulo,
        objetivo=objetivo,
        horas_por_dia=horas_por_dia,
        semanas=semanas,
        plano=plano_llm,
        meta=meta,
        etapas=etapas,
    )

    trilha = get_trilha(trilha_id)
    if not trilha:
        raise RuntimeError("Falha ao carregar trilha recém-criada.")
    return trilha


def gerar_sala(
    trilha_id: int,
    *,
    etapa_id: Optional[int] = None,
    regenerar: bool = False,
    instrucoes_extras: Optional[str] = None,
) -> dict[str, Any]:
    trilha = get_trilha(trilha_id)
    if not trilha:
        raise KeyError(f"trilha_id {trilha_id} não encontrado")

    if etapa_id is not None:
        from app.trilha_storage import get_etapa

        etapa = get_etapa(etapa_id)
        if not etapa or etapa["trilha_id"] != trilha_id:
            raise KeyError(f"etapa_id {etapa_id} não pertence à trilha {trilha_id}")
    else:
        etapa = get_etapa_atual(trilha_id)
        if not etapa:
            raise ValueError("Trilha sem etapa atual — todas concluídas ou plano vazio.")

    if not regenerar:
        existente = get_sala_aberta_etapa(trilha_id, int(etapa["id"]))
        if existente:
            return existente

    documento_id = int(trilha["documento_id"])
    desempenho = get_desempenho_documento(documento_id)

    sala_llm = generate_sala_dia(
        etapa=etapa,
        documento_id=documento_id,
        horas_por_dia=float(trilha.get("horas_por_dia") or 1),
        desempenho=desempenho,
        instrucoes_extras=instrucoes_extras,
    )

    atividades = []
    for i, a in enumerate(sala_llm.get("atividades") or [], start=1):
        payload = dict(a.get("payload") or {})
        payload.setdefault("documento_id", documento_id)
        if etapa.get("tema"):
            payload.setdefault("tema", etapa["tema"])
        if etapa.get("palavras_chave"):
            payload.setdefault("palavras_chave", etapa["palavras_chave"])
        if etapa.get("pagina_inicio") is not None:
            payload.setdefault("pagina_inicio", etapa["pagina_inicio"])
        if etapa.get("pagina_fim") is not None:
            payload.setdefault("pagina_fim", etapa["pagina_fim"])

        tipo = a.get("tipo", "ler")
        if tipo == "questoes":
            payload.setdefault(
                "gerar_url",
                f"/gerar/documento/{documento_id}",
            )
        elif tipo == "revisar_erradas":
            tema_q = payload.get("tema") or etapa.get("tema") or ""
            payload.setdefault(
                "banco_url",
                f"/banco/questoes?documento_id={documento_id}&so_erradas=true",
            )
            if tema_q:
                payload["banco_url"] += f"&busca={tema_q}"

        atividades.append(
            {
                "ordem": i,
                "tipo": tipo,
                "titulo": a.get("titulo") or f"Atividade {i}",
                "descricao": a.get("descricao"),
                "duracao_minutos": a.get("duracao_minutos"),
                "payload": payload,
            }
        )

    meta_sala = {
        "modelo": effective_bedrock_model_id(),
        "etapa_id": etapa["id"],
        "etapa_ordem": etapa["ordem"],
        "desempenho_snapshot": desempenho,
    }

    sala_id = create_sala(
        trilha_id=trilha_id,
        etapa_id=int(etapa["id"]),
        dia_numero=int(etapa["ordem"]),
        titulo=sala_llm.get("titulo") or f"Sala — {etapa.get('titulo', 'Estudo')}",
        resumo=sala_llm.get("resumo"),
        meta=meta_sala,
        atividades=atividades,
    )

    sala = get_sala(sala_id)
    if not sala:
        raise RuntimeError("Falha ao carregar sala recém-criada.")
    return sala


def obter_sala_hoje(trilha_id: int, **kwargs) -> dict[str, Any]:
    """Retorna sala criada hoje ou gera uma nova."""
    existente = get_sala_hoje(trilha_id)
    if existente:
        return existente
    return gerar_sala(trilha_id, **kwargs)


def avancar_etapa_trilha(trilha_id: int) -> dict[str, Any]:
    trilha = avancar_trilha(trilha_id)
    if not trilha:
        raise KeyError(f"trilha_id {trilha_id} não encontrado")
    return trilha
