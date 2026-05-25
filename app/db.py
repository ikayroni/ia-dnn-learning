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

CREATE INDEX IF NOT EXISTS idx_doc_hash ON documentos(hash_sha256);
CREATE INDEX IF NOT EXISTS idx_doc_job ON documentos(ocr_job_id);
CREATE INDEX IF NOT EXISTS idx_ger_doc ON geracoes(documento_id);
CREATE INDEX IF NOT EXISTS idx_q_ger ON questoes(geracao_id);
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


def init_db() -> None:
    _ensure_dir()
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()
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
