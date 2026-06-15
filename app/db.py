"""SQLite local: documentos, gerações e questões."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import BASE_DIR

DB_DIR = BASE_DIR / "data"
DB_PATH = DB_DIR / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome_arquivo TEXT NOT NULL,
    hash_sha256 TEXT UNIQUE,
    paginas INTEGER,
    caracteres INTEGER,
    ocr_job_id TEXT,
    fonte TEXT,                       -- "ocr", "pdf_nativo", "texto"
    criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS geracoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    documento_id INTEGER REFERENCES documentos(id) ON DELETE CASCADE,
    tema TEXT,
    palavras_chave TEXT,              -- JSON list
    pagina_inicio INTEGER,
    pagina_fim INTEGER,
    tipos TEXT,                       -- JSON list
    dificuldade TEXT,
    instrucoes_extras TEXT,
    num_questoes_por_chunk INTEGER,
    max_chunks INTEGER,
    idioma TEXT,
    estilo TEXT,
    num_alternativas INTEGER,
    incluir_explicacao INTEGER,
    modelo TEXT,
    meta_json TEXT,
    questoes_count INTEGER DEFAULT 0,
    criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS questoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    geracao_id INTEGER NOT NULL REFERENCES geracoes(id) ON DELETE CASCADE,
    ordem INTEGER NOT NULL,
    tipo TEXT NOT NULL,
    enunciado TEXT NOT NULL,
    alternativas_json TEXT,
    gabarito TEXT NOT NULL,
    dificuldade TEXT,
    chunk_id INTEGER,
    pagina_inicio INTEGER,
    pagina_fim INTEGER,
    explicacao TEXT,
    explicacoes_alternativas_json TEXT,
    referencia TEXT,
    idioma TEXT,
    estilo TEXT
);

CREATE TABLE IF NOT EXISTS tentativas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    questao_id INTEGER NOT NULL REFERENCES questoes(id) ON DELETE CASCADE,
    resposta_usuario TEXT NOT NULL,
    acertou INTEGER NOT NULL,          -- 0/1
    tempo_resposta_ms INTEGER,
    comentario TEXT,
    criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_doc_hash ON documentos(hash_sha256);
CREATE INDEX IF NOT EXISTS idx_doc_job ON documentos(ocr_job_id);
CREATE INDEX IF NOT EXISTS idx_ger_doc ON geracoes(documento_id);
CREATE INDEX IF NOT EXISTS idx_q_ger ON questoes(geracao_id);
CREATE INDEX IF NOT EXISTS idx_tent_q ON tentativas(questao_id);
CREATE INDEX IF NOT EXISTS idx_tent_data ON tentativas(criado_em DESC);

CREATE TABLE IF NOT EXISTS trilhas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    documento_id INTEGER REFERENCES documentos(id) ON DELETE SET NULL,
    titulo TEXT NOT NULL,
    objetivo TEXT,
    horas_por_dia REAL,
    semanas INTEGER,
    etapa_atual INTEGER NOT NULL DEFAULT 1,
    plano_json TEXT NOT NULL DEFAULT '{}',
    meta_json TEXT,
    status TEXT NOT NULL DEFAULT 'ativa',
    origem TEXT NOT NULL DEFAULT 'ia',   -- "ia" | "manual"
    criado_em TEXT NOT NULL DEFAULT (datetime('now')),
    atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trilha_etapas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trilha_id INTEGER NOT NULL REFERENCES trilhas(id) ON DELETE CASCADE,
    ordem INTEGER NOT NULL,
    modulo TEXT,
    titulo TEXT NOT NULL,
    objetivo TEXT,
    conteudo TEXT,                        -- material/instruções escritos pelo professor
    pagina_inicio INTEGER,
    pagina_fim INTEGER,
    tema TEXT,
    palavras_chave TEXT,
    duracao_minutos INTEGER,
    status TEXT NOT NULL DEFAULT 'pendente',
    concluida_em TEXT
);

CREATE TABLE IF NOT EXISTS salas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trilha_id INTEGER NOT NULL REFERENCES trilhas(id) ON DELETE CASCADE,
    etapa_id INTEGER REFERENCES trilha_etapas(id) ON DELETE SET NULL,
    dia_numero INTEGER,
    titulo TEXT,
    resumo TEXT,
    meta_json TEXT,
    status TEXT NOT NULL DEFAULT 'aberta',
    criado_em TEXT NOT NULL DEFAULT (datetime('now')),
    concluida_em TEXT
);

CREATE TABLE IF NOT EXISTS sala_atividades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sala_id INTEGER NOT NULL REFERENCES salas(id) ON DELETE CASCADE,
    ordem INTEGER NOT NULL,
    tipo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    descricao TEXT,
    duracao_minutos INTEGER,
    payload_json TEXT,
    status TEXT NOT NULL DEFAULT 'pendente',
    concluida_em TEXT
);

CREATE INDEX IF NOT EXISTS idx_trilha_doc ON trilhas(documento_id);
CREATE INDEX IF NOT EXISTS idx_etapa_trilha ON trilha_etapas(trilha_id, ordem);
CREATE INDEX IF NOT EXISTS idx_sala_trilha ON salas(trilha_id);
CREATE INDEX IF NOT EXISTS idx_ativ_sala ON sala_atividades(sala_id, ordem);

-- Flash cards (estilo NotebookLM) + repetição espaçada (SM-2)
CREATE TABLE IF NOT EXISTS flashcard_decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    documento_id INTEGER REFERENCES documentos(id) ON DELETE SET NULL,
    titulo TEXT NOT NULL,
    descricao TEXT,
    tema TEXT,
    idioma TEXT,
    fonte TEXT,                       -- "ia_texto", "ia_pdf", "ia_ocr", "ia_documento", "manual"
    modelo TEXT,
    meta_json TEXT,
    criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS flashcards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES flashcard_decks(id) ON DELETE CASCADE,
    ordem INTEGER NOT NULL,
    frente TEXT NOT NULL,
    verso TEXT NOT NULL,
    dica TEXT,
    tags_json TEXT,
    dificuldade TEXT,
    referencia TEXT,
    chunk_id INTEGER,
    pagina_inicio INTEGER,
    pagina_fim INTEGER,
    -- estado SRS
    repeticoes INTEGER NOT NULL DEFAULT 0,
    intervalo_dias INTEGER NOT NULL DEFAULT 0,
    ease_factor REAL NOT NULL DEFAULT 2.5,
    due_em TEXT NOT NULL DEFAULT (datetime('now')),
    ultima_revisao_em TEXT,
    lapsos INTEGER NOT NULL DEFAULT 0,
    total_revisoes INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS flashcard_revisoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flashcard_id INTEGER NOT NULL REFERENCES flashcards(id) ON DELETE CASCADE,
    nota INTEGER NOT NULL,             -- 0=errei,1=dificil,2=bom,3=facil
    intervalo_anterior INTEGER,
    intervalo_novo INTEGER,
    ease_factor REAL,
    tempo_resposta_ms INTEGER,
    criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_deck_doc ON flashcard_decks(documento_id);
CREATE INDEX IF NOT EXISTS idx_card_deck ON flashcards(deck_id, ordem);
CREATE INDEX IF NOT EXISTS idx_card_due ON flashcards(due_em);
CREATE INDEX IF NOT EXISTS idx_rev_card ON flashcard_revisoes(flashcard_id);
CREATE INDEX IF NOT EXISTS idx_rev_data ON flashcard_revisoes(criado_em DESC);
"""


# Colunas adicionadas em versoes mais novas; aplicadas via ALTER TABLE se faltarem.
_MIGRATIONS = {
    "geracoes": [
        ("idioma", "TEXT"),
        ("estilo", "TEXT"),
        ("num_alternativas", "INTEGER"),
        ("incluir_explicacao", "INTEGER"),
    ],
    "questoes": [
        ("explicacao", "TEXT"),
        ("explicacoes_alternativas_json", "TEXT"),
        ("referencia", "TEXT"),
        ("idioma", "TEXT"),
        ("estilo", "TEXT"),
    ],
    "trilhas": [
        ("origem", "TEXT NOT NULL DEFAULT 'ia'"),
    ],
    "trilha_etapas": [
        ("conteudo", "TEXT"),
    ],
}


def _ensure_dir() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, cols in _MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col_name, col_type in cols:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
    conn.commit()


def _migrate_trilhas_documento_opcional(conn: sqlite3.Connection) -> None:
    """Bancos antigos têm trilhas.documento_id NOT NULL; recria a tabela para
    permitir trilhas manuais sem material vinculado."""
    info = conn.execute("PRAGMA table_info(trilhas)").fetchall()
    if not info:
        return
    doc_col = next((r for r in info if r["name"] == "documento_id"), None)
    if doc_col is None or doc_col["notnull"] == 0:
        return  # já é opcional (ou tabela nova)

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE trilhas__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento_id INTEGER REFERENCES documentos(id) ON DELETE SET NULL,
            titulo TEXT NOT NULL,
            objetivo TEXT,
            horas_por_dia REAL,
            semanas INTEGER,
            etapa_atual INTEGER NOT NULL DEFAULT 1,
            plano_json TEXT NOT NULL DEFAULT '{}',
            meta_json TEXT,
            status TEXT NOT NULL DEFAULT 'ativa',
            origem TEXT NOT NULL DEFAULT 'ia',
            criado_em TEXT NOT NULL DEFAULT (datetime('now')),
            atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO trilhas__new
            (id, documento_id, titulo, objetivo, horas_por_dia, semanas,
             etapa_atual, plano_json, meta_json, status, criado_em, atualizado_em)
        SELECT id, documento_id, titulo, objetivo, horas_por_dia, semanas,
               etapa_atual, COALESCE(plano_json, '{}'), meta_json, status,
               criado_em, atualizado_em
        FROM trilhas;
        DROP TABLE trilhas;
        ALTER TABLE trilhas__new RENAME TO trilhas;
        CREATE INDEX IF NOT EXISTS idx_trilha_doc ON trilhas(documento_id);
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def init_db() -> None:
    _ensure_dir()
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate_trilhas_documento_opcional(conn)
        _apply_migrations(conn)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    _ensure_dir()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
