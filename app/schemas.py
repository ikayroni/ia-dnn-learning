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


class TemaItem(BaseModel):
    titulo: str
    palavras_chave: List[str] = Field(default_factory=list)


class TemasResponse(BaseModel):
    temas: List[TemaItem]
    paginas: Optional[int] = None
    caracteres_amostrados: Optional[int] = None
    modelo: Optional[str] = None
    ocr_job_id: Optional[str] = None
