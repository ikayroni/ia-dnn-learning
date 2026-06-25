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


class TraduzirItemIn(BaseModel):
    id: str = Field(..., description="Identificador da questão (ecoado na resposta)")
    enunciado: str = Field(default="")
    alternativas: List[str] = Field(default_factory=list)
    explicacao: Optional[str] = None


class TraduzirItemOut(BaseModel):
    id: str
    enunciado: str = ""
    alternativas: List[str] = Field(default_factory=list)
    explicacao: Optional[str] = None


class TraduzirRequest(BaseModel):
    idioma: Literal["pt", "en"] = Field(default="pt", description="Idioma de destino")
    itens: List[TraduzirItemIn] = Field(..., min_length=1, max_length=30)


class TraduzirResponse(BaseModel):
    idioma: str
    itens: List[TraduzirItemOut]


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
    conteudo: Optional[str] = None
    pagina_inicio: Optional[int] = None
    pagina_fim: Optional[int] = None
    tema: Optional[str] = None
    palavras_chave: List[str] = Field(default_factory=list)
    duracao_minutos: Optional[int] = None
    status: str
    concluida_em: Optional[str] = None


class TrilhaOut(BaseModel):
    id: int
    documento_id: Optional[int] = None
    titulo: str
    objetivo: Optional[str] = None
    horas_por_dia: Optional[float] = None
    semanas: Optional[int] = None
    etapa_atual: int
    plano: Dict[str, Any] = Field(default_factory=dict)
    meta: Optional[Dict[str, Any]] = None
    status: str
    origem: Optional[str] = None
    criado_em: Optional[str] = None
    atualizado_em: Optional[str] = None
    etapas: Optional[List[TrilhaEtapaOut]] = None


class TrilhaEstudoStats(BaseModel):
    trilha_id: Optional[int] = None
    cards_total: int = 0
    cards_due: int = 0
    cards_novos: int = 0
    etapa_pendente: bool = False
    etapa_atual_titulo: Optional[str] = None
    itens_hoje: int = 0


class TrilhaResumoOut(BaseModel):
    id: int
    documento_id: Optional[int] = None
    titulo: str
    objetivo: Optional[str] = None
    horas_por_dia: Optional[float] = None
    semanas: Optional[int] = None
    etapa_atual: int
    status: str
    origem: Optional[str] = None
    criado_em: Optional[str] = None
    atualizado_em: Optional[str] = None
    total_etapas: int
    etapas_concluidas: int
    estudo: Optional[TrilhaEstudoStats] = None


class TrilhaEstudoResponse(BaseModel):
    trilha_id: int
    titulo: str
    documento_id: Optional[int] = None
    etapa_atual: int
    total_itens: int = 0
    cards_due: int = 0
    cards_novos: int = 0
    itens_hoje: int = 0
    itens: List[Dict[str, Any]] = Field(default_factory=list)


class TrilhasListResponse(BaseModel):
    trilhas: List[TrilhaResumoOut]
    total: int


# --- Modo professor: criar/editar trilha manualmente ---


class EtapaManualIn(BaseModel):
    modulo: Optional[str] = Field(default=None, max_length=120)
    titulo: str = Field(..., min_length=1, max_length=200)
    objetivo: Optional[str] = Field(default=None, max_length=1000)
    conteudo: Optional[str] = Field(
        default=None, description="Material/instruções escritos pelo professor (texto livre)."
    )
    pagina_inicio: Optional[int] = Field(default=None, ge=0)
    pagina_fim: Optional[int] = Field(default=None, ge=0)
    tema: Optional[str] = Field(default=None, max_length=200)
    palavras_chave: List[str] = Field(default_factory=list)
    duracao_minutos: Optional[int] = Field(default=None, ge=0, le=1440)


class TrilhaManualCreate(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=200)
    objetivo: Optional[str] = Field(default=None, max_length=500)
    documento_id: Optional[int] = Field(
        default=None, ge=1, description="Material opcional vinculado à trilha."
    )
    resumo: Optional[str] = Field(default=None, max_length=1000)
    horas_por_dia: Optional[float] = Field(default=1.0, ge=0.25, le=12)
    semanas: Optional[int] = Field(default=None, ge=1, le=52)
    etapas: List[EtapaManualIn] = Field(default_factory=list)


class TrilhaUpdate(BaseModel):
    titulo: Optional[str] = Field(default=None, min_length=1, max_length=200)
    objetivo: Optional[str] = Field(default=None, max_length=500)
    horas_por_dia: Optional[float] = Field(default=None, ge=0.25, le=12)
    semanas: Optional[int] = Field(default=None, ge=1, le=52)
    status: Optional[str] = Field(default=None, max_length=30)
    documento_id: Optional[int] = Field(default=None, ge=1)


class EtapaUpdate(BaseModel):
    """Edição da etapa: conteúdo e/ou status (todos opcionais)."""

    modulo: Optional[str] = Field(default=None, max_length=120)
    titulo: Optional[str] = Field(default=None, min_length=1, max_length=200)
    objetivo: Optional[str] = Field(default=None, max_length=1000)
    conteudo: Optional[str] = None
    pagina_inicio: Optional[int] = Field(default=None, ge=0)
    pagina_fim: Optional[int] = Field(default=None, ge=0)
    tema: Optional[str] = Field(default=None, max_length=200)
    palavras_chave: Optional[List[str]] = None
    duracao_minutos: Optional[int] = Field(default=None, ge=0, le=1440)
    status: Optional[StatusEtapaTrilha] = None


class EtapasReorder(BaseModel):
    ordem: List[int] = Field(..., description="IDs das etapas na nova ordem.")


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


# ---------------------------------------------------------------------------
# Flash cards (estilo NotebookLM): frente/verso gerados do material + estudo
# com repetição espaçada (SM-2).
# ---------------------------------------------------------------------------

# Nota de auto-avaliação no estudo → qualidade SM-2.
# 0 = Errei (again) | 1 = Difícil (hard) | 2 = Bom (good) | 3 = Fácil (easy)
NotaRevisao = Literal[0, 1, 2, 3]


class Flashcard(BaseModel):
    id: Optional[int] = Field(
        default=None,
        description="ID no banco (presente após salvar o deck ou ao carregar do histórico).",
    )
    frente: str = Field(..., description="Pergunta / termo / conceito (lado da frente).")
    verso: str = Field(..., description="Resposta / definição (lado de trás).")
    dica: Optional[str] = Field(default=None, description="Dica opcional exibida sob demanda.")
    tags: List[str] = Field(default_factory=list)
    dificuldade: Optional[Dificuldade] = None
    referencia: Optional[str] = Field(
        default=None,
        description="Trecho/citação do material que fundamenta o card.",
    )
    fonte: Optional[FonteQuestao] = None
    # Estado SRS (preenchido ao carregar do banco)
    repeticoes: Optional[int] = None
    intervalo_dias: Optional[int] = None
    ease_factor: Optional[float] = None
    due_em: Optional[str] = None
    ultima_revisao_em: Optional[str] = None
    lapsos: Optional[int] = None
    total_revisoes: Optional[int] = None


class FlashcardsGerarTextoRequest(BaseModel):
    texto: str = Field(..., min_length=50, description="Conteúdo base para os flashcards")
    num_flashcards_por_chunk: int = Field(default=5, ge=1, le=20)
    max_chunks: Optional[int] = Field(default=None, ge=1, le=50)
    tema: Optional[str] = Field(default=None, description="Foco temático dos cards")
    palavras_chave: Optional[List[str]] = Field(
        default=None, description="Filtra trechos contendo pelo menos uma das palavras"
    )
    pagina_inicio: Optional[int] = Field(default=None, ge=1)
    pagina_fim: Optional[int] = Field(default=None, ge=1)
    instrucoes_extras: Optional[str] = Field(default=None, max_length=500)
    idioma: Idioma = Field(default="pt", description="pt | en | it")
    titulo: Optional[str] = Field(default=None, max_length=200, description="Título do deck")


class FlashcardManualIn(BaseModel):
    frente: str = Field(..., min_length=1)
    verso: str = Field(..., min_length=1)
    dica: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    dificuldade: Optional[Dificuldade] = None
    referencia: Optional[str] = None


class DeckCriarRequest(BaseModel):
    """Cria um deck vazio ou com cards manuais (sem IA)."""

    titulo: str = Field(..., min_length=1, max_length=200)
    descricao: Optional[str] = Field(default=None, max_length=1000)
    tema: Optional[str] = None
    idioma: Idioma = Field(default="pt")
    documento_id: Optional[int] = Field(default=None, ge=1)
    cards: List[FlashcardManualIn] = Field(default_factory=list)


class FlashcardUpdate(BaseModel):
    frente: Optional[str] = Field(default=None, min_length=1)
    verso: Optional[str] = Field(default=None, min_length=1)
    dica: Optional[str] = None
    tags: Optional[List[str]] = None
    dificuldade: Optional[Dificuldade] = None
    referencia: Optional[str] = None


class FlashcardOut(Flashcard):
    deck_id: int
    ordem: int


class DeckResumoOut(BaseModel):
    id: int
    documento_id: Optional[int] = None
    titulo: str
    descricao: Optional[str] = None
    tema: Optional[str] = None
    idioma: Optional[str] = None
    fonte: Optional[str] = None
    modelo: Optional[str] = None
    nome_arquivo: Optional[str] = None
    criado_em: Optional[str] = None
    total_cards: int = 0
    cards_due: int = 0
    cards_novos: int = 0


class DeckOut(DeckResumoOut):
    meta: Optional[Dict[str, Any]] = None
    cards: List[FlashcardOut] = Field(default_factory=list)


class DecksListResponse(BaseModel):
    decks: List[DeckResumoOut]
    total: int


class FlashcardsGerarResponse(BaseModel):
    deck: DeckOut
    meta: Dict[str, Any]


class RevisaoIn(BaseModel):
    nota: NotaRevisao = Field(
        ..., description="0=Errei, 1=Difícil, 2=Bom, 3=Fácil (auto-avaliação)"
    )
    tempo_resposta_ms: Optional[int] = Field(default=None, ge=0)


class RevisaoResultado(BaseModel):
    flashcard_id: int
    nota: int
    intervalo_anterior_dias: Optional[int] = None
    intervalo_novo_dias: int
    repeticoes: int
    ease_factor: float
    due_em: str
    lapsos: int
    total_revisoes: int


class EstudoResponse(BaseModel):
    deck_id: Optional[int] = None
    total_due: int
    cards: List[FlashcardOut] = Field(default_factory=list)


class FlashcardsEstatisticas(BaseModel):
    decks: int
    cards: int
    revisoes: int
    cards_due_hoje: int
    cards_novos: int
    cards_dominados: int
    por_dificuldade: Dict[str, int] = Field(default_factory=dict)
    por_idioma: Dict[str, int] = Field(default_factory=dict)


class FlashcardProgresso(FlashcardOut):
    acertos: int = 0
    erros: int = 0
    ultima_nota: Optional[int] = None
    status: str = "novo"  # novo | aprendendo | dominado


class DeckProgressoOut(BaseModel):
    id: int
    titulo: str
    nome_arquivo: Optional[str] = None
    tema: Optional[str] = None
    total_cards: int = 0
    cards_due: int = 0
    cards_novos: int = 0
    cards_revisados: int = 0
    cards_dominados: int = 0
    total_revisoes: int = 0
    cards: List[FlashcardProgresso] = Field(default_factory=list)


class RevisaoHistoricoItem(BaseModel):
    id: int
    nota: int
    intervalo_anterior: Optional[int] = None
    intervalo_novo: Optional[int] = None
    ease_factor: Optional[float] = None
    tempo_resposta_ms: Optional[int] = None
    criado_em: Optional[str] = None


class RevisoesHistoricoResponse(BaseModel):
    flashcard_id: int
    revisoes: List[RevisaoHistoricoItem] = Field(default_factory=list)
