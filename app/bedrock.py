from __future__ import annotations

import json
import re

import boto3
from botocore.exceptions import ClientError

from app.config import effective_bedrock_model_id, settings

TIPOS_QUESTAO = ("multipla_escolha", "verdadeiro_falso", "dissertativa")


def _build_system_prompt() -> str:
    tipos = ", ".join(TIPOS_QUESTAO)
    return f"""Você é um especialista em avaliação educacional.
Gere questões EXCLUSIVAMENTE com base no texto fornecido pelo usuário.
Não invente fatos que não estejam no texto.
Responda APENAS com JSON válido, sem markdown, sem explicações fora do JSON.
Tipos permitidos: {tipos}.
Para multipla_escolha: exatamente 4 alternativas (A, B, C, D) e gabarito como letra.
Para verdadeiro_falso: alternativas ["Verdadeiro", "Falso"] e gabarito "Verdadeiro" ou "Falso".
Para dissertativa: alternativas null e gabarito com resposta esperada resumida."""


def _build_user_prompt(
    chunk_text: str,
    num_questions: int,
    tipos: list[str],
    dificuldade: str | None,
    chunk_id: int,
    page_start: int | None,
    page_end: int | None,
    tema: str | None = None,
    instrucoes_extras: str | None = None,
) -> str:
    tipos_str = ", ".join(tipos)
    diff_line = f"Dificuldade preferida: {dificuldade}.\n" if dificuldade else ""
    page_info = ""
    if page_start is not None:
        page_info = f"Páginas aproximadas do trecho: {page_start}"
        if page_end and page_end != page_start:
            page_info += f"–{page_end}"
        page_info += ".\n"

    tema_line = ""
    if tema and tema.strip():
        tema_line = (
            f"FOCO/TEMA: gere questões somente sobre \"{tema.strip()}\".\n"
            "Se o trecho NÃO contiver informação sobre o tema, retorne {\"questoes\": []}.\n"
        )

    extras_line = ""
    if instrucoes_extras and instrucoes_extras.strip():
        extras_line = f"Instruções adicionais: {instrucoes_extras.strip()}\n"

    return f"""{diff_line}{page_info}{tema_line}{extras_line}chunk_id para fonte: {chunk_id}.
Gere até {num_questions} questão(ões) dos tipos: {tipos_str}.

Formato JSON obrigatório:
{{
  "questoes": [
    {{
      "tipo": "multipla_escolha",
      "enunciado": "...",
      "alternativas": ["...", "...", "...", "..."],
      "gabarito": "B",
      "dificuldade": "facil|media|dificil",
      "fonte": {{ "chunk_id": {chunk_id}, "pagina_inicio": {page_start or "null"}, "pagina_fim": {page_end or "null"} }}
    }}
  ]
}}

TEXTO:
{chunk_text}"""


def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _extract_text_from_converse(response: dict) -> str:
    message = response.get("output", {}).get("message", {})
    parts = []
    for block in message.get("content", []):
        if block.get("text"):
            parts.append(block["text"])
    if not parts:
        raise RuntimeError("Resposta vazia do Bedrock (Converse API)")
    return "".join(parts)


def _handle_bedrock_error(model_id: str, code: str, msg: str) -> None:
    lower = msg.lower()
    if code == "AccessDeniedException":
        raise RuntimeError(
            f"Acesso negado ao Bedrock ({model_id}). "
            "Habilite o modelo em Bedrock → Model access (mesma região do .env)."
        )
    if "inference profile" in lower:
        raise RuntimeError(
            f"Erro Bedrock: {msg} Use inference profile no .env (ex.: us.amazon.nova-lite-v1:0)."
        )
    if "legacy" in lower or "upgrade to an active model" in lower:
        raise RuntimeError(
            f"Erro Bedrock: {msg} "
            "O Haiku/Claude legado está bloqueado. No .env troque para um modelo ATIVO, ex.: "
            "BEDROCK_MODEL_ID=us.amazon.nova-lite-v1:0 "
            "ou global.anthropic.claude-sonnet-4-20250514-v1:0 (veja Bedrock → Model catalog)."
        )
    if "use case details" in lower:
        raise RuntimeError(
            f"Erro Bedrock: {msg} Preencha o formulário Anthropic em Bedrock → Model access."
        )
    raise RuntimeError(f"Erro Bedrock: {msg}")


def invoke_bedrock(
    chunk_text: str,
    *,
    num_questions: int = 2,
    tipos: list[str] | None = None,
    dificuldade: str | None = None,
    chunk_id: int = 0,
    page_start: int | None = None,
    page_end: int | None = None,
    tema: str | None = None,
    instrucoes_extras: str | None = None,
) -> dict:
    tipos = tipos or ["multipla_escolha"]
    for t in tipos:
        if t not in TIPOS_QUESTAO:
            raise ValueError(f"Tipo inválido: {t}. Use: {TIPOS_QUESTAO}")

    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    user_prompt = _build_user_prompt(
        chunk_text,
        num_questions,
        tipos,
        dificuldade,
        chunk_id,
        page_start,
        page_end,
        tema=tema,
        instrucoes_extras=instrucoes_extras,
    )
    system_prompt = _build_system_prompt()
    model_id = effective_bedrock_model_id()

    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": user_prompt}],
                }
            ],
            inferenceConfig={
                "maxTokens": 4096,
                "temperature": 0.3,
            },
        )
    except ClientError as e:
        err = e.response.get("Error", {})
        _handle_bedrock_error(model_id, err.get("Code", ""), err.get("Message", str(e)))

    raw_text = _extract_text_from_converse(response)
    return _parse_json_response(raw_text)


def discover_topics(
    sample_text: str,
    *,
    max_topics: int = 10,
) -> list[dict]:
    """Pede ao LLM uma lista resumida de temas do documento."""
    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    model_id = effective_bedrock_model_id()

    system = (
        "Você analisa textos didáticos e identifica os principais temas/tópicos abordados. "
        "Responda APENAS com JSON válido, sem markdown."
    )
    user = f"""Liste os {max_topics} principais temas presentes no texto abaixo.
Use rótulos curtos (1–5 palavras), em português, sem repetir, ordenados do mais ao menos central.

Formato JSON exigido:
{{
  "temas": [
    {{ "titulo": "...", "palavras_chave": ["...", "..."] }}
  ]
}}

TEXTO (amostra):
{sample_text[:8000]}"""

    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": 1500, "temperature": 0.2},
        )
    except ClientError as e:
        err = e.response.get("Error", {})
        _handle_bedrock_error(model_id, err.get("Code", ""), err.get("Message", str(e)))

    raw = _extract_text_from_converse(response)
    data = _parse_json_response(raw)
    temas = data.get("temas", [])
    if not isinstance(temas, list):
        return []
    return temas[:max_topics]
