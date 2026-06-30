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
    from app.console_io import safe_print

    try:
        safe_print(f"[bedrock] {msg}")
    except Exception:
        pass

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


def _quer_vinheta_clinica(estilo: str, incluir_caso_clinico: bool | None) -> bool:
    if incluir_caso_clinico is not None:
        return incluir_caso_clinico
    return estilo in (
        "clinico",
        "diagnostico",
        "conduta",
        "cirurgia",
        "pediatria",
        "obstetricia",
        "emergencia",
    )


def _linha_formato_enunciado(idioma: str, com_vinheta: bool) -> str:
    if com_vinheta:
        if idioma == "it":
            return (
                "FORMATO ENUNCIADO (OBBLIGATORIO):\n"
                "- Il campo JSON \"enunciado\" deve contenere PRIMA la vignetta clinica completa "
                "(eta, sesso, anamnesi, esame obiettivo ed eventuali esami complementari coerenti col testo).\n"
                "- Dopo la vignetta, inserisci la domanda finale (es. diagnosi, condotta, terapia).\n"
                "- NON usare domande teoriche secche senza presentare un paziente/caso.\n"
                "- Non creare campi JSON separati per il caso: tutto va in \"enunciado\".\n"
            )
        if idioma == "en":
            return (
                "STATEMENT FORMAT (MANDATORY):\n"
                "- The JSON field \"enunciado\" must FIRST contain the full clinical vignette "
                "(age, sex, history, physical exam and relevant tests grounded in the source text).\n"
                "- After the vignette, add the final question.\n"
                "- Do NOT ask bare theoretical questions without a clinical case.\n"
                "- Do not use separate JSON fields for the case — everything goes in \"enunciado\".\n"
            )
        return (
            "FORMATO DO ENUNCIADO (OBRIGATÓRIO):\n"
            "- O campo JSON \"enunciado\" deve conter PRIMEIRO a vinheta clínica completa "
            "(idade, sexo, queixa, história, exame físico e/ou exames complementares coerentes com o texto).\n"
            "- Depois da vinheta, inclua a pergunta final (diagnóstico, conduta, tratamento etc.).\n"
            "- NÃO faça perguntas teóricas secas sem apresentar um caso clínico.\n"
            "- Não use campos JSON separados para o caso — tudo vai em \"enunciado\".\n"
        )
    if idioma == "it":
        return (
            "FORMATO ENUNCIADO: domanda diretta sul contenuto, SENZA vignetta di paziente immaginario.\n"
        )
    if idioma == "en":
        return (
            "STATEMENT FORMAT: direct question about the content, WITHOUT a fictional patient vignette.\n"
        )
    return (
        "FORMATO DO ENUNCIADO: pergunta direta sobre o conteúdo, SEM vinheta de paciente fictício.\n"
    )


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
        "Para CADA questão de múltipla escolha inclua OBRIGATORIAMENTE: \n"
        "  - \"explicacao\": explicação DETALHADA (3–6 frases) dizendo POR QUE o gabarito é correto, "
        "com raciocínio clínico passo a passo baseado no texto.\n"
        "  - \"explicacoes_alternativas\": objeto com chaves "
        + letras
        + " explicando POR QUE cada distratora está errada (mínimo 1 frase por letra).\n"
        "  - \"referencia\": citação LITERAL ou quase literal (até 400 caracteres) do trecho do texto-fonte "
        "que sustenta a resposta — copie o trecho relevante do material fornecido."
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
    incluir_caso_clinico: bool | None = None,
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
    enunciado_line = _linha_formato_enunciado(
        idioma, _quer_vinheta_clinica(estilo, incluir_caso_clinico)
    )

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

    com_vinheta = _quer_vinheta_clinica(estilo, incluir_caso_clinico)
    if com_vinheta:
        if idioma == "it":
            enun_example = (
                '"Un paziente di 62 anni, maschio, si presenta con dispnea progressiva da 3 giorni... '
                '[vignetta completa]. Quale diagnosi è più probabile?"'
            )
        elif idioma == "en":
            enun_example = (
                '"A 62-year-old man presents with progressive dyspnea for 3 days... '
                '[full vignette]. What is the most likely diagnosis?"'
            )
        else:
            enun_example = (
                '"Paciente de 62 anos, sexo masculino, apresenta dispneia progressiva há 3 dias... '
                '[vinheta completa]. Qual o diagnóstico mais provável?"'
            )
    else:
        enun_example = '"Pergunta direta sobre o conteúdo do texto, sem vinheta de paciente."'

    return f"""{diff_line}{page_info}{tema_line}{extras_line}{estilo_line}{enunciado_line}chunk_id para fonte: {chunk_id}.
Gere até {num_questions} questão(ões) dos tipos: {tipos_str}.
Cada múltipla escolha deve ter {num_alternativas} alternativas ({letras_str}).

Formato JSON obrigatório (sem markdown):
{{
  "questoes": [
    {{
      "tipo": "multipla_escolha",
      "enunciado": {enun_example},
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
            "Habilite o modelo em Bedrock -> Model access (mesma regiao do .env)."
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
            "ou global.anthropic.claude-sonnet-4-20250514-v1:0 (veja Bedrock -> Model catalog)."
        )
    if "use case details" in lower:
        raise RuntimeError(
            f"Erro Bedrock: {msg} Preencha o formulario Anthropic em Bedrock -> Model access."
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
    incluir_caso_clinico: bool | None = None,
    temperature: float = 0.3,
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
        incluir_caso_clinico,
    )
    system_prompt = _build_system_prompt(idioma, num_alternativas, incluir_explicacao)
    model_id = effective_bedrock_model_id()

    n = max(1, num_questions)
    if incluir_explicacao:
        max_tokens = min(16000, 1400 * n + 800)
    else:
        max_tokens = min(8000, 480 * n + 400)
    _blog(
        f"converse -> modelo={model_id} chunk_id={chunk_id} "
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
                "temperature": temperature,
            },
        )
    except ReadTimeoutError as e:
        _blog(f"TIMEOUT apos {time.time() - t0:.1f}s chunk_id={chunk_id}")
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
        f"converse OK em {elapsed:.1f}s | tokens in={usage.get('inputTokens')} "
        f"out={usage.get('outputTokens')} | chars_out={len(raw_text)}"
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


def _converse_json(system: str, user: str, *, max_tokens: int = 4000, temperature: float = 0.25) -> dict:
    client = boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        config=_BEDROCK_CFG,
    )
    model_id = effective_bedrock_model_id()
    t0 = time.time()
    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
    except ReadTimeoutError as e:
        raise RuntimeError(
            "Bedrock demorou mais de 120s (timeout). Tente menos semanas ou um material menor."
        ) from e
    except ClientError as e:
        err = e.response.get("Error", {})
        _handle_bedrock_error(model_id, err.get("Code", ""), err.get("Message", str(e)))
    elapsed = time.time() - t0
    raw = _extract_text_from_converse(response)
    usage = response.get("usage", {})
    _blog(
        f"converse JSON em {elapsed:.1f}s | in={usage.get('inputTokens')} "
        f"out={usage.get('outputTokens')}"
    )
    return _parse_json_response(raw)


def traduzir_questoes(
    itens: list[dict],
    *,
    idioma_destino: str = "pt",
) -> dict:
    """Traduz uma lista de questões (enunciado, alternativas e, se houver, explicação)
    preservando o sentido clínico e a estrutura. Idioma de origem é detectado
    automaticamente (o material costuma estar em italiano).

    Cada item de entrada: {"id": str, "enunciado": str, "alternativas": [str, ...],
    "explicacao": str | None}. Retorna {"itens": [ {mesma estrutura traduzida} ]}.
    """
    idioma_nome = IDIOMA_NOMES.get(idioma_destino, IDIOMA_NOMES["pt"])
    if not isinstance(itens, list) or not itens:
        return {"itens": []}

    payload = []
    for it in itens:
        payload.append(
            {
                "id": str(it.get("id", "")),
                "enunciado": it.get("enunciado") or "",
                "alternativas": it.get("alternativas") or [],
                "explicacao": it.get("explicacao") or None,
            }
        )

    system = f"""Você é um tradutor médico profissional especializado em questões de provas
de Residência/Revalida/USMLE. Traduza FIELMENTE para {idioma_nome}.
Regras:
- Detecte automaticamente o idioma de origem (normalmente italiano).
- Traduza enunciado, TODAS as alternativas e a explicação (quando houver).
- NÃO altere o sentido clínico, valores, doses, siglas de exames ou o gabarito.
- Use terminologia médica padrão do idioma de destino.
- Mantenha a MESMA quantidade e ORDEM das alternativas.
- NÃO inclua letras (A), B)...) no texto das alternativas; traduza apenas o conteúdo.
- Preserve o campo "id" exatamente como recebido.
Responda APENAS com JSON válido (sem markdown, sem comentários, sem texto fora do JSON)."""

    user = f"""Traduza para {idioma_nome} os itens abaixo.

Formato JSON obrigatório de saída (sem markdown):
{{
  "itens": [
    {{ "id": "...", "enunciado": "...", "alternativas": ["...", "..."], "explicacao": "... ou null" }}
  ]
}}

ITENS (JSON):
{json.dumps(payload, ensure_ascii=False)}"""

    data = _converse_json(system, user, max_tokens=6000, temperature=0.15)
    out = data.get("itens", [])
    if not isinstance(out, list):
        return {"itens": []}
    return {"itens": out}


def gerar_flashcards(
    chunk_text: str,
    *,
    num_flashcards: int = 5,
    chunk_id: int = 0,
    page_start: int | None = None,
    page_end: int | None = None,
    tema: str | None = None,
    instrucoes_extras: str | None = None,
    idioma: str = "pt",
    exatamente: bool = False,
) -> dict:
    """Gera flashcards (frente/verso) a partir de um trecho do material via Bedrock.

    Retorna {"flashcards": [{frente, verso, dica, tags, dificuldade, referencia, fonte}]}.
    """
    idioma_nome = IDIOMA_NOMES.get(idioma, IDIOMA_NOMES["pt"])

    system = f"""Você cria FLASHCARDS de estudo objetivos para memorização (estilo Anki) em medicina.
Gere os cards EXCLUSIVAMENTE com base no TEXTO fornecido — não invente fatos fora do texto.
Idioma OBRIGATÓRIO de frente e verso: {idioma_nome}.
Responda APENAS com JSON válido (sem markdown, sem comentários, sem texto fora do JSON).

Princípios:
- FRENTE: pergunta direta, uma ideia por card (máx. 2 frases).
- VERSO: resposta curta e memorável (1–3 frases ou lista curta).
- Foco em recall ativo; evite parágrafos longos e cards triviais.
- NÃO inclua dicas mnemônicas nem citações de trecho no verso.
- Use "dica": null e "referencia": null sempre."""

    diff_line = ""
    tema_line = ""
    if tema and tema.strip():
        tema_line = (
            f'FOCO/TEMA: gere flashcards somente sobre "{tema.strip()}".\n'
            'Se o trecho NÃO contiver informação sobre o tema, retorne {"flashcards": []}.\n'
        )
    extras_line = ""
    if instrucoes_extras and instrucoes_extras.strip():
        extras_line = f"Instruções adicionais: {instrucoes_extras.strip()}\n"
    page_info = ""
    if page_start is not None:
        page_info = f"Páginas aproximadas do trecho: {page_start}"
        if page_end and page_end != page_start:
            page_info += f"–{page_end}"
        page_info += ".\n"

    qtd_line = (
        f"Gere EXATAMENTE {num_flashcards} flashcard(s) distintos"
        if exatamente
        else f"Gere até {num_flashcards} flashcard(s)"
    )
    user = f"""{diff_line}{page_info}{tema_line}{extras_line}chunk_id para fonte: {chunk_id}.
{qtd_line} de alta qualidade a partir do TEXTO.

Formato JSON obrigatório (sem markdown):
{{
  "flashcards": [
    {{
      "frente": "...",
      "verso": "...",
      "dica": null,
      "tags": ["...", "..."],
      "dificuldade": "facil|media|dificil",
      "referencia": null,
      "fonte": {{ "chunk_id": {chunk_id}, "pagina_inicio": {page_start or "null"}, "pagina_fim": {page_end or "null"} }}
    }}
  ]
}}

TEXTO:
{chunk_text}"""

    data = _converse_json(system, user, max_tokens=4000, temperature=0.3)
    cards = data.get("flashcards", [])
    if not isinstance(cards, list):
        return {"flashcards": []}
    return {"flashcards": cards}


def generate_trilha_plano(
    *,
    sample_text: str,
    paginas: Optional[int],
    temas: list[dict],
    objetivo: str,
    semanas: int,
    horas_por_dia: float,
    dias_estudo: int,
    instrucoes_extras: Optional[str] = None,
) -> dict:
    """Gera plano estruturado da trilha (etapas diárias)."""
    temas_txt = json.dumps(temas, ensure_ascii=False) if temas else "[]"
    extras = f"\nInstruções adicionais: {instrucoes_extras}\n" if instrucoes_extras else ""
    minutos_dia = int(horas_por_dia * 60)

    system = (
        "Você é um planejador de estudos médicos (Revalida, Residência, USMLE). "
        "Monte trilhas realistas com base no material fornecido. "
        "Responda APENAS com JSON válido, sem markdown."
    )
    user = f"""Crie um plano de estudos com exatamente {dias_estudo} etapas (uma por dia de estudo).

Contexto:
- Objetivo do aluno: {objetivo}
- Duração: {semanas} semana(s), ~{horas_por_dia} h/dia (~{minutos_dia} min/dia por etapa)
- Páginas totais do material (se conhecido): {paginas or "desconhecido"}
- Temas identificados no material: {temas_txt}
{extras}
Regras:
- Cada etapa deve ter páginas coerentes (pagina_inicio <= pagina_fim) quando o material tiver páginas.
- Distribua o conteúdo ao longo dos {dias_estudo} dias sem pular grandes lacunas.
- Inclua tema e 2–5 palavras_chave por etapa para gerar questões depois.
- duracao_minutos por etapa: próximo de {minutos_dia} (pode variar ±15 min).

Formato JSON obrigatório:
{{
  "titulo": "título curto da trilha",
  "resumo": "1–3 frases sobre o plano",
  "etapas": [
    {{
      "dia": 1,
      "modulo": "nome do módulo/bloco",
      "titulo": "título da etapa",
      "objetivo": "o que o aluno deve dominar",
      "pagina_inicio": 1,
      "pagina_fim": 30,
      "tema": "tema principal",
      "palavras_chave": ["kw1", "kw2"],
      "duracao_minutos": {minutos_dia}
    }}
  ]
}}

TEXTO DO MATERIAL (amostra):
{sample_text[:10000]}"""

    data = _converse_json(system, user, max_tokens=6000, temperature=0.25)
    etapas = data.get("etapas", [])
    if not isinstance(etapas, list) or not etapas:
        raise ValueError("LLM não retornou etapas válidas para a trilha.")
    return data


def generate_sala_dia(
    *,
    etapa: dict,
    documento_id: int,
    horas_por_dia: float,
    desempenho: Optional[dict] = None,
    instrucoes_extras: Optional[str] = None,
) -> dict:
    """Gera atividades da sala de estudo para uma etapa."""
    minutos_dia = int(horas_por_dia * 60)
    desempenho_txt = json.dumps(desempenho or {}, ensure_ascii=False)
    extras = f"\nInstruções: {instrucoes_extras}\n" if instrucoes_extras else ""
    pag_i = etapa.get("pagina_inicio")
    pag_f = etapa.get("pagina_fim")
    tema = etapa.get("tema") or etapa.get("titulo", "")
    kws = etapa.get("palavras_chave") or []

    system = (
        "Você monta sessões de estudo médico com atividades concretas e acionáveis. "
        "Responda APENAS com JSON válido, sem markdown."
    )
    user = f"""Monte a sala de estudo do dia para esta etapa:

Etapa: {json.dumps(etapa, ensure_ascii=False)}
documento_id (para APIs): {documento_id}
Tempo total alvo: ~{minutos_dia} minutos
Desempenho prévio do aluno neste material: {desempenho_txt}
{extras}
Tipos de atividade permitidos (campo "tipo"):
- ler: leitura de páginas do material
- questoes: praticar com questões geradas (use payload com tema, palavras_chave, num_questoes)
- revisar_erradas: revisar questões que errou no banco
- resumo: bullets do conteúdo do dia (campo bullets no payload)
- reflexao: 2–3 perguntas para autoavaliação (campo perguntas no payload)

Regras:
- Entre 3 e 6 atividades, somando duracao_minutos próximo de {minutos_dia}.
- Sempre inclua pelo menos: 1x ler, 1x questoes.
- Se desempenho.temas_fracos existir, inclua revisar_erradas.
- payload deve incluir documento_id, pagina_inicio, pagina_fim, tema, palavras_chave quando aplicável.
- Para questoes: num_questoes entre 5 e 15, estilo sugerido "clinico".

Formato JSON:
{{
  "titulo": "Sala — ...",
  "resumo": "motivação em 1–2 frases",
  "atividades": [
    {{
      "tipo": "ler",
      "titulo": "...",
      "descricao": "...",
      "duracao_minutos": 25,
      "payload": {{
        "documento_id": {documento_id},
        "pagina_inicio": {pag_i or 1},
        "pagina_fim": {pag_f or 1},
        "tema": "{tema}",
        "palavras_chave": {json.dumps(kws, ensure_ascii=False)}
      }}
    }}
  ]
}}"""

    data = _converse_json(system, user, max_tokens=3500, temperature=0.3)
    atividades = data.get("atividades", [])
    if not isinstance(atividades, list) or not atividades:
        raise ValueError("LLM não retornou atividades válidas para a sala.")
    return data
