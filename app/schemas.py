from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

TipoQuestao = Literal["multipla_escolha", "verdadeiro_falso", "dissertativa"]
Dificuldade = Literal["facil", "media", "dificil"]
Idioma = Literal["pt", "en", "it"]
EstiloQuestao = Literal[
    "geral",
    "clinico",
    "diagnostico",
    "conduta",
    "farmacologia",
    "cirurgia",
    "pediatria",
    "obstetricia",
    "emergencia",
    "saude_publica",
    "imagem",
]


class FonteQuestao(BaseModel):
    chunk_id: Optional[int] = None
    pagina_inicio: Optional[int] = None
    pagina_fim: Optional[int] = None


class Questao(BaseModel):
    id: Optional[int] = Field(
        default=None,
        description="ID no banco (presente após salvar a geração ou ao carregar do histórico).",
    )
    tipo: TipoQuestao
    enunciado: str
    alternativas: Optional[List[str]] = None
    gabarito: str
    dificuldade: Optional[Dificuldade] = None
    fonte: Optional[FonteQuestao] = None
    explicacao: Optional[str] = Field(
        default=None,
        description="Por que o gabarito é a alternativa correta (resposta comentada).",
    )
    explicacoes_alternativas: Optional[Dict[str, str]] = Field(
        default=None,
        description="Por que cada distratora está errada. Chaves: A, B, C, D, (E).",
    )
    referencia: Optional[str] = Field(
        default=None,
        description="Trecho/citação do texto-base que fundamenta a resposta.",
    )
    idioma: Optional[Idioma] = None
    estilo: Optional[EstiloQuestao] = None


class GerarTextoRequest(BaseModel):
    texto: str = Field(..., min_length=50, description="Conteúdo base para as questões")
    num_questoes_por_chunk: int = Field(default=2, ge=1, le=10)
    tipos: List[TipoQuestao] = Field(default=["multipla_escolha"])
    dificuldade: Optional[Dificuldade] = None
    max_chunks: Optional[int] = Field(default=None, ge=1, le=50)
    tema: Optional[str] = Field(default=None, description="Foco temático para as questões")
    palavras_chave: Optional[List[str]] = Field(
        default=None,
        description="Filtra trechos contendo pelo menos uma das palavras",
    )
    pagina_inicio: Optional[int] = Field(default=None, ge=1)
    pagina_fim: Optional[int] = Field(default=None, ge=1)
    instrucoes_extras: Optional[str] = Field(default=None, max_length=500)

    idioma: Idioma = Field(default="pt", description="pt | en | it")
    estilo: EstiloQuestao = Field(
        default="clinico",
        description="Formato pedagógico (caso clínico, conduta, diagnóstico, etc.).",
    )
    num_alternativas: int = Field(
        default=5,
        ge=2,
        le=6,
        description="Quantidade de alternativas em múltipla escolha (4 = A–D, 5 = A–E).",
    )
    incluir_explicacao: bool = Field(
        default=True, description="Pedir resposta comentada para cada questão."
    )


class GerarResponse(BaseModel):
    questoes: List[Questao]
    meta: Dict[str, Any]


class OcrJobCreated(BaseModel):
    job_id: str
    status: str
    message: str
    pagina_inicio: Optional[int] = None
    pagina_fim: Optional[int] = None
    total_paginas_arquivo: Optional[int] = None
    poll_url: str


class OcrJobStatus(BaseModel):
    job_id: str
    status: str
    phase: Optional[str] = None
    filename: Optional[str] = None
    pagina_inicio: Optional[int] = None
    pagina_fim: Optional[int] = None
    total_paginas_arquivo: Optional[int] = None
    paginas_ocr: Optional[int] = None
    caracteres: Optional[int] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    elapsed_seconds: Optional[int] = None
    textract_polls: Optional[int] = None
    textract_status: Optional[str] = None
    textract_elapsed_seconds: Optional[int] = None
    textract_job_id: Optional[str] = None
    gerar_questoes_url: Optional[str] = None


class OcrJobsList(BaseModel):
    jobs: List["OcrJobStatus"]
    total: int


class QuestaoUpdate(BaseModel):
    """Campos opcionais para PATCH /banco/questoes/{id} — envie só o que mudou."""

    tipo: Optional[TipoQuestao] = None
    enunciado: Optional[str] = Field(default=None, min_length=1)
    alternativas: Optional[List[str]] = None
    gabarito: Optional[str] = Field(default=None, min_length=1)
    dificuldade: Optional[Dificuldade] = None
    explicacao: Optional[str] = None
    explicacoes_alternativas: Optional[Dict[str, str]] = None
    referencia: Optional[str] = None
    idioma: Optional[Idioma] = None
    estilo: Optional[EstiloQuestao] = None
    fonte: Optional[FonteQuestao] = None


class TentativaIn(BaseModel):
    resposta: str = Field(..., min_length=1, description="Letra (A–F) ou texto da resposta")
    tempo_resposta_ms: Optional[int] = Field(default=None, ge=0)
    comentario: Optional[str] = Field(default=None, max_length=2000)


class TentativaResultado(BaseModel):
    tentativa_id: int
    acertou: bool
    gabarito: str
    explicacao: Optional[str] = None
    explicacoes_alternativas: Optional[Dict[str, str]] = None


class TemaItem(BaseModel):
    titulo: str
    palavras_chave: List[str] = Field(default_factory=list)


class TemasResponse(BaseModel):
    temas: List[TemaItem]
    paginas: Optional[int] = None
    caracteres_amostrados: Optional[int] = None
    modelo: Optional[str] = None
    ocr_job_id: Optional[str] = None


TipoAtividadeTrilha = Literal[
    "ler", "questoes", "revisar_erradas", "resumo", "reflexao"
]
StatusEtapaTrilha = Literal["pendente", "em_andamento", "concluida"]
StatusAtividadeSala = Literal["pendente", "concluida", "ignorada"]


class TrilhaGerarRequest(BaseModel):
    documento_id: int = Field(..., ge=1, description="Documento no histórico (vincula a trilha)")
    ocr_job_id: Optional[str] = Field(
        default=None,
        description="Job OCR concluído (status succeeded). Usa se o documento não tiver ocr_job_id salvo.",
    )
    texto: Optional[str] = Field(
        default=None,
        min_length=50,
        description="Texto do material (mín. 50 chars). Usa quando não há OCR/cache — ex.: colar trecho do PDF.",
    )
    objetivo: str = Field(
        default="Revalida / Residência Médica",
        max_length=200,
        description="Meta do estudo (prova, especialidade, etc.)",
    )
    semanas: int = Field(default=2, ge=1, le=52)
    horas_por_dia: float = Field(default=1.0, ge=0.25, le=12)
    dias_por_semana: int = Field(
        default=5, ge=1, le=7, description="Dias de estudo por semana (ex.: 5 = seg–sex)"
    )
    max_temas: int = Field(default=12, ge=3, le=20)
    instrucoes_extras: Optional[str] = Field(default=None, max_length=500)


class TrilhaEtapaOut(BaseModel):
    id: int
    trilha_id: int
    ordem: int
    modulo: Optional[str] = None
    titulo: str
    objetivo: Optional[str] = None
    pagina_inicio: Optional[int] = None
    pagina_fim: Optional[int] = None
    tema: Optional[str] = None
    palavras_chave: List[str] = Field(default_factory=list)
    duracao_minutos: Optional[int] = None
    status: str
    concluida_em: Optional[str] = None


class TrilhaOut(BaseModel):
    id: int
    documento_id: int
    titulo: str
    objetivo: Optional[str] = None
    horas_por_dia: Optional[float] = None
    semanas: Optional[int] = None
    etapa_atual: int
    plano: Dict[str, Any] = Field(default_factory=dict)
    meta: Optional[Dict[str, Any]] = None
    status: str
    criado_em: Optional[str] = None
    atualizado_em: Optional[str] = None
    etapas: Optional[List[TrilhaEtapaOut]] = None


class TrilhaResumoOut(BaseModel):
    id: int
    documento_id: int
    titulo: str
    objetivo: Optional[str] = None
    horas_por_dia: Optional[float] = None
    semanas: Optional[int] = None
    etapa_atual: int
    status: str
    criado_em: Optional[str] = None
    atualizado_em: Optional[str] = None
    total_etapas: int
    etapas_concluidas: int


class TrilhasListResponse(BaseModel):
    trilhas: List[TrilhaResumoOut]
    total: int


class SalaGerarRequest(BaseModel):
    etapa_id: Optional[int] = Field(
        default=None, description="Etapa específica; omita para usar etapa_atual da trilha"
    )
    regenerar: bool = Field(
        default=False,
        description="Se true, cria nova sala mesmo com sala aberta na etapa",
    )
    instrucoes_extras: Optional[str] = Field(default=None, max_length=500)


class SalaAtividadeOut(BaseModel):
    id: int
    sala_id: int
    ordem: int
    tipo: str
    titulo: str
    descricao: Optional[str] = None
    duracao_minutos: Optional[int] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    status: str
    concluida_em: Optional[str] = None


class SalaOut(BaseModel):
    id: int
    trilha_id: int
    etapa_id: Optional[int] = None
    dia_numero: Optional[int] = None
    titulo: Optional[str] = None
    resumo: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    status: str
    criado_em: Optional[str] = None
    concluida_em: Optional[str] = None
    atividades: List[SalaAtividadeOut] = Field(default_factory=list)


class SalaResumoOut(BaseModel):
    id: int
    trilha_id: int
    etapa_id: Optional[int] = None
    dia_numero: Optional[int] = None
    titulo: Optional[str] = None
    resumo: Optional[str] = None
    status: str
    criado_em: Optional[str] = None
    total_atividades: int
    atividades_concluidas: int


class SalasListResponse(BaseModel):
    salas: List[SalaResumoOut]


class EtapaStatusUpdate(BaseModel):
    status: StatusEtapaTrilha


class AtividadeStatusUpdate(BaseModel):
    status: StatusAtividadeSala
