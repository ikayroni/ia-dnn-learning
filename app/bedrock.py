from __future__ import annotations

import json
import re
import sys
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError

from app.config import effective_bedrock_model_id, settings


_BEDROCK_CFG = Config(
    read_timeout=120,
    connect_timeout=10,
    retries={"max_attempts": 2, "mode": "standard"},
)


def _blog(msg: str) -> None:
    print(f"[bedrock] {msg}", flush=True, file=sys.stdout)

TIPOS_QUESTAO = ("multipla_escolha", "verdadeiro_falso", "dissertativa")

LETRAS = ["A", "B", "C", "D", "E", "F"]


IDIOMA_NOMES = {
    "pt": "Português (Brasil)",
    "en": "English",
    "it": "Italiano",
}


ESTILO_DESC_PT = {
    "geral": "Questões teóricas objetivas baseadas no conteúdo.",
    "clinico": (
        "CASO CLÍNICO no estilo das provas de Residência Médica/Revalida: "
        "construa uma vinheta com paciente (idade, sexo, queixa, história, exame físico, "
        "exames complementares quando pertinente) e pergunte o desfecho mais provável."
    ),
    "diagnostico": (
        "Caso clínico curto onde a pergunta final é o DIAGNÓSTICO mais provável."
    ),
    "conduta": (
        "Caso clínico onde já há diagnóstico ou forte hipótese e a pergunta final é "
        "a CONDUTA inicial/seguinte mais apropriada."
    ),
    "farmacologia": (
        "Questões focadas em FARMACOLOGIA: mecanismo de ação, indicação, contraindicação, "
        "efeitos adversos, interação ou ajuste de dose, sempre ancoradas no texto."
    ),
    "cirurgia": (
        "Caso CIRÚRGICO: indicação cirúrgica, técnica, complicações pós-operatórias ou "
        "manejo perioperatório."
    ),
    "pediatria": (
        "Caso PEDIÁTRICO com paciente em idade infantil/adolescente (informe idade, peso "
        "quando relevante, e considerações típicas da pediatria)."
    ),
    "obstetricia": (
        "Caso de OBSTETRÍCIA/GINECOLOGIA: gestante (idade gestacional), pré-natal, "
        "trabalho de parto, puerpério, ginecologia clínica."
    ),
    "emergencia": (
        "Caso de EMERGÊNCIA/PRONTO-SOCORRO: priorize ABCDE, classificação de risco, "
        "tempo-resposta e conduta imediata."
    ),
    "saude_publica": (
        "Questões de SAÚDE PÚBLICA / Medicina Preventiva: epidemiologia, vigilância, "
        "SUS, indicadores, prevenção."
    ),
    "imagem": (
        "Questão centrada em INTERPRETAÇÃO de exame complementar (laboratório/imagem) "
        "descrito no texto-base."
    ),
}


def _estilo_traduzido(estilo: str, idioma: str) -> str:
    base = ESTILO_DESC_PT.get(estilo, ESTILO_DESC_PT["geral"])
    if idioma == "pt":
        return base
    if idioma == "en":
        return (
            "Style: " + base + " (Generate the question itself entirely in English, "
            "using internationally recognized medical terms.)"
        )
    if idioma == "it":
        return (
            "Stile: " + base + " (Genera la domanda interamente in italiano, "
            "usando terminologia medica standard.)"
        )
    return base


def _build_system_prompt(idioma: str, num_alternativas: int, incluir_explicacao: bool) -> str:
    tipos = ", ".join(TIPOS_QUESTAO)
    letras = ", ".join(LETRAS[:num_alternativas])
    idioma_nome = IDIOMA_NOMES.get(idioma, IDIOMA_NOMES["pt"])
    expl_rule = (
        "Para CADA questão de múltipla escolha inclua: \n"
        "  - \"explicacao\": parágrafo objetivo dizendo POR QUE o gabarito é a alternativa correta, citando o conteúdo do texto.\n"
        "  - \"explicacoes_alternativas\": objeto com chaves "
        + letras
        + " explicando POR QUE cada distratora está errada (a chave do gabarito também recebe um reforço breve).\n"
        "  - \"referencia\": citação curta (até 240 caracteres) do trecho do texto que sustenta a resposta."
    ) if incluir_explicacao else "Campos de explicação não são obrigatórios."

    return f"""Você é um banco de questões médico de alto nível (estilo Revalida / Residência / USMLE).
Gere questões EXCLUSIVAMENTE com base no TEXTO fornecido pelo usuário.
Não invente dados clínicos, dosagens ou condutas que não estejam apoiados pelo texto.
Idioma de saída OBRIGATÓRIO para enunciado, alternativas e explicações: {idioma_nome}.
Responda APENAS com JSON válido (sem markdown, sem comentários, sem texto fora do JSON).
Tipos permitidos: {tipos}.

Para multipla_escolha:
  - exatamente {num_alternativas} alternativas, identificadas pelas letras {letras}.
  - apenas UMA correta.
  - gabarito como LETRA ({letras}).
  - distratores plausíveis (erros clássicos / armadilhas comuns).
{expl_rule}

Para verdadeiro_falso:
  - alternativas ["Verdadeiro","Falso"] (ou equivalente no idioma escolhido), gabarito uma das duas.
  - inclua "explicacao" obrigatoriamente.

Para dissertativa:
  - alternativas = null; gabarito = resposta esperada resumida (3–6 linhas).
  - inclua "explicacao" com os pontos-chave que a resposta deve abordar."""


def _build_user_prompt(
    chunk_text: str,
    num_questions: int,
    tipos: list[str],
    dificuldade: str | None,
    chunk_id: int,
    page_start: int | None,
    page_end: int | None,
    tema: str | None,
    instrucoes_extras: str | None,
    idioma: str,
    estilo: str,
    num_alternativas: int,
    incluir_explicacao: bool,
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

    estilo_line = f"ESTILO PEDAGÓGICO: {_estilo_traduzido(estilo, idioma)}\n"

    letras = LETRAS[:num_alternativas]
    letras_str = ", ".join(letras)
    alts_example = "[" + ", ".join(f'"texto da {l}"' for l in letras) + "]"
    expl_alts_example = "{" + ", ".join(f'"{l}": "..."' for l in letras) + "}"

    expl_fields = ""
    if incluir_explicacao:
        expl_fields = f""",
      "explicacao": "...",
      "explicacoes_alternativas": {expl_alts_example},
      "referencia": "..." """

    return f"""{diff_line}{page_info}{tema_line}{extras_line}{estilo_line}chunk_id para fonte: {chunk_id}.
Gere até {num_questions} questão(ões) dos tipos: {tipos_str}.
Cada múltipla escolha deve ter {num_alternativas} alternativas ({letras_str}).

Formato JSON obrigatório (sem markdown):
{{
  "questoes": [
    {{
      "tipo": "multipla_escolha",
      "enunciado": "...",
      "alternativas": {alts_example},
      "gabarito": "{letras[0]}",
      "dificuldade": "facil|media|dificil",
      "fonte": {{ "chunk_id": {chunk_id}, "pagina_inicio": {page_start or "null"}, "pagina_fim": {page_end or "null"} }}{expl_fields}
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
    idioma: str = "pt",
    estilo: str = "clinico",
    num_alternativas: int = 5,
    incluir_explicacao: bool = True,
) -> dict:
    tipos = tipos or ["multipla_escolha"]
    for t in tipos:
        if t not in TIPOS_QUESTAO:
            raise ValueError(f"Tipo inválido: {t}. Use: {TIPOS_QUESTAO}")
    if not (2 <= num_alternativas <= len(LETRAS)):
        raise ValueError(f"num_alternativas deve estar entre 2 e {len(LETRAS)}")

    client = boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        config=_BEDROCK_CFG,
    )
    user_prompt = _build_user_prompt(
        chunk_text,
        num_questions,
        tipos,
        dificuldade,
        chunk_id,
        page_start,
        page_end,
        tema,
        instrucoes_extras,
        idioma,
        estilo,
        num_alternativas,
        incluir_explicacao,
    )
    system_prompt = _build_system_prompt(idioma, num_alternativas, incluir_explicacao)
    model_id = effective_bedrock_model_id()

    max_tokens = 4500 if incluir_explicacao else 2500
    _blog(
        f"converse → modelo={model_id} chunk_id={chunk_id} "
        f"chars_in={len(user_prompt) + len(system_prompt)} max_tokens={max_tokens}"
    )
    t0 = time.time()
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
                "maxTokens": max_tokens,
                "temperature": 0.3,
            },
        )
    except ReadTimeoutError as e:
        _blog(f"TIMEOUT após {time.time() - t0:.1f}s chunk_id={chunk_id}")
        raise RuntimeError(
            "Bedrock demorou mais de 120s (timeout). Reduza o tamanho do trecho "
            "(CHUNK_SIZE_CHARS no .env) ou desligue 'Resposta comentada'."
        ) from e
    except ClientError as e:
        err = e.response.get("Error", {})
        _blog(f"ClientError {err.get('Code')}: {err.get('Message')}")
        _handle_bedrock_error(model_id, err.get("Code", ""), err.get("Message", str(e)))

    elapsed = time.time() - t0
    raw_text = _extract_text_from_converse(response)
    usage = response.get("usage", {})
    _blog(
        f"converse OK em {elapsed:.1f}s · tokens in={usage.get('inputTokens')} "
        f"out={usage.get('outputTokens')} · chars_out={len(raw_text)}"
    )
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
