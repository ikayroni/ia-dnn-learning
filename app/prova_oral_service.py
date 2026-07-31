"""Orquestração da prova oral: geração de casos e avaliação via Bedrock."""

from __future__ import annotations

from app.bedrock import avaliar_resposta_oral, gerar_caso_oral
from app.prova_oral_storage import (
    DISCIPLINAS,
    create_sessao,
    get_estatisticas,
    get_sessao,
    get_sessao_ativa,
    list_sessoes,
    salvar_avaliacao,
)


def listar_disciplinas() -> list[dict]:
    return DISCIPLINAS


def iniciar_sessao(
    disciplina: str,
    *,
    nivel: str = "intermediario",
    usuario_email: str | None = None,
) -> dict:
    valid = {d["id"] for d in DISCIPLINAS}
    if disciplina not in valid:
        raise ValueError(f"Disciplina inválida: {disciplina}")

    caso = gerar_caso_oral(disciplina, nivel=nivel)
    sessao = create_sessao(
        disciplina=disciplina,
        titulo=str(caso.get("titulo", "Caso clínico")),
        enunciado=str(caso.get("enunciado", "")),
        resumo=caso.get("resumo"),
        tags=caso.get("tags") if isinstance(caso.get("tags"), list) else [],
        nivel=nivel,
        tempo_minutos=int(caso.get("tempo_minutos") or 15),
        usuario_email=usuario_email,
    )
    sessao["perguntas_guia"] = caso.get("perguntas_guia") or []
    return sessao


def avaliar_sessao(sessao_id: int, resposta_texto: str) -> dict:
    texto = (resposta_texto or "").strip()
    if len(texto) < 20:
        raise ValueError("Resposta muito curta. Descreva sua conduta com mais detalhes.")

    sessao = get_sessao(sessao_id)
    if not sessao:
        raise ValueError("Sessão não encontrada")
    if sessao.get("status") == "concluido":
        return sessao

    caso = {
        "disciplina": sessao.get("disciplina"),
        "disciplina_label": sessao.get("disciplina_label"),
        "titulo": sessao.get("titulo"),
        "enunciado": sessao.get("enunciado"),
    }
    resultado = avaliar_resposta_oral(caso, texto)
    rubrica = resultado.get("rubrica", [])
    if not isinstance(rubrica, list):
        rubrica = []

    atualizada = salvar_avaliacao(
        sessao_id,
        resposta_texto=texto,
        rubrica=rubrica,
        feedback=str(resultado.get("feedback", "")),
        espelho_resposta=str(resultado.get("espelho_resposta", "")),
        nota_geral=int(resultado.get("nota_geral", 0)),
    )
    if not atualizada:
        raise RuntimeError("Falha ao salvar avaliação")
    return atualizada


def dashboard(usuario_email: str | None = None) -> dict:
    return {
        "disciplinas": listar_disciplinas(),
        "estatisticas": get_estatisticas(usuario_email),
        "sessao_ativa": get_sessao_ativa(usuario_email),
        "sessoes": list_sessoes(usuario_email=usuario_email, limit=15),
    }
