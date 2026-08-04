"""Orquestração da prova oral: catálogo pré-pronto e avaliação via Bedrock."""

from __future__ import annotations

import re

from app.bedrock import avaliar_resposta_oral, gerar_caso_oral
from app.prova_oral_catalog import (
    create_catalogo,
    create_questao,
    ensure_seed_catalogos,
    get_catalogo,
    get_questao,
    list_catalogos,
    list_questoes,
    pick_questao_aleatoria,
)
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


def _enunciado_da_pergunta(pergunta: str) -> str:
    pergunta = pergunta.strip()
    return (
        "La commissione d'esame ti pone la seguente domanda:\n\n"
        f"**{pergunta}**\n\n"
        "Rispondi oralmente in italiano, in modo chiaro e strutturato."
    )


def _titulo_curto(pergunta: str, max_len: int = 80) -> str:
    texto = re.sub(r"\s+", " ", pergunta.strip())
    if len(texto) <= max_len:
        return texto
    return texto[: max_len - 1].rstrip() + "…"


def _sessao_from_questao(
    questao: dict,
    *,
    catalogo: dict | None = None,
    usuario_email: str | None = None,
    nivel: str = "intermediario",
) -> dict:
    disciplina = (catalogo or {}).get("disciplina") or "medicina-legale"
    tempo = int(questao.get("tempo_minutos") or (catalogo or {}).get("tempo_minutos") or 10)
    pergunta = str(questao.get("pergunta", ""))
    return create_sessao(
        disciplina=disciplina,
        titulo=_titulo_curto(pergunta),
        enunciado=_enunciado_da_pergunta(pergunta),
        resumo="Rispondi alla domanda come in una prova orale reale del Revalida Italia.",
        tags=questao.get("tags") if isinstance(questao.get("tags"), list) else [],
        nivel=nivel,
        tempo_minutos=tempo,
        usuario_email=usuario_email,
        questao_id=int(questao["id"]),
        catalogo_id=int(questao.get("catalogo_id") or (catalogo or {}).get("id") or 0) or None,
        resposta_esperada=str(questao.get("resposta_esperada", "")),
    )


def iniciar_sessao(
    *,
    disciplina: str | None = None,
    nivel: str = "intermediario",
    usuario_email: str | None = None,
    questao_id: int | None = None,
    catalogo_id: int | None = None,
) -> dict:
    ensure_seed_catalogos()

    if questao_id:
        questao = get_questao(questao_id)
        if not questao or not questao.get("ativo", 1):
            raise ValueError("Domanda non trovata")
        catalogo = get_catalogo(int(questao["catalogo_id"]))
        return _sessao_from_questao(questao, catalogo=catalogo, usuario_email=usuario_email, nivel=nivel)

    if catalogo_id:
        catalogo = get_catalogo(catalogo_id)
        if not catalogo:
            raise ValueError("Prova non trovata")
        questao = pick_questao_aleatoria(catalogo_id)
        if not questao:
            raise ValueError("Nessuna domanda disponibile in questa prova")
        return _sessao_from_questao(questao, catalogo=catalogo, usuario_email=usuario_email, nivel=nivel)

    if disciplina:
        valid = {d["id"] for d in DISCIPLINAS}
        if disciplina not in valid:
            raise ValueError(f"Disciplina non valida: {disciplina}")

        catalogos = list_catalogos(disciplina=disciplina)
        if catalogos:
            cat = catalogos[0]
            questao = pick_questao_aleatoria(int(cat["id"]))
            if questao:
                return _sessao_from_questao(questao, catalogo=cat, usuario_email=usuario_email, nivel=nivel)

        caso = gerar_caso_oral(disciplina, nivel=nivel)
        sessao = create_sessao(
            disciplina=disciplina,
            titulo=str(caso.get("titulo", "Caso clinico")),
            enunciado=str(caso.get("enunciado", "")),
            resumo=caso.get("resumo"),
            tags=caso.get("tags") if isinstance(caso.get("tags"), list) else [],
            nivel=nivel,
            tempo_minutos=int(caso.get("tempo_minutos") or 15),
            usuario_email=usuario_email,
        )
        sessao["perguntas_guia"] = caso.get("perguntas_guia") or []
        return sessao

    raise ValueError("Specificare disciplina, catalogo_id o questao_id")


def avaliar_sessao(sessao_id: int, resposta_texto: str) -> dict:
    texto = (resposta_texto or "").strip()
    if len(texto) < 20:
        raise ValueError("Risposta troppo breve. Descrivi la tua risposta con più dettagli.")

    sessao = get_sessao(sessao_id)
    if not sessao:
        raise ValueError("Sessione non trovata")
    if sessao.get("status") == "concluido":
        return sessao

    caso = {
        "disciplina": sessao.get("disciplina"),
        "disciplina_label": sessao.get("disciplina_label"),
        "titulo": sessao.get("titulo"),
        "enunciado": sessao.get("enunciado"),
        "resposta_esperada": sessao.get("resposta_esperada"),
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
        raise RuntimeError("Errore nel salvataggio della valutazione")
    return atualizada


def dashboard(usuario_email: str | None = None) -> dict:
    ensure_seed_catalogos()
    catalogos = list_catalogos()
    catalogos_com_questoes = []
    for cat in catalogos:
        item = dict(cat)
        item["questoes"] = list_questoes(int(cat["id"]))
        catalogos_com_questoes.append(item)
    return {
        "disciplinas": listar_disciplinas(),
        "catalogos": catalogos_com_questoes,
        "estatisticas": get_estatisticas(usuario_email),
        "sessao_ativa": get_sessao_ativa(usuario_email),
        "sessoes": list_sessoes(usuario_email=usuario_email, limit=15),
    }


def criar_prova(
    *,
    slug: str,
    titulo: str,
    disciplina: str,
    descricao: str | None = None,
    fonte: str | None = None,
    tempo_minutos: int = 10,
) -> dict:
    valid = {d["id"] for d in DISCIPLINAS}
    if disciplina not in valid:
        raise ValueError(f"Disciplina non valida: {disciplina}")
    return create_catalogo(
        slug=slug,
        titulo=titulo,
        disciplina=disciplina,
        descricao=descricao,
        fonte=fonte,
        tempo_minutos=tempo_minutos,
    )


def adicionar_questao_prova(
    catalogo_id: int,
    *,
    pergunta: str,
    resposta_esperada: str,
    ordem: int = 0,
    tags: list[str] | None = None,
    tempo_minutos: int | None = None,
) -> dict:
    return create_questao(
        catalogo_id,
        pergunta=pergunta,
        resposta_esperada=resposta_esperada,
        ordem=ordem,
        tags=tags,
        tempo_minutos=tempo_minutos,
    )
