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
    pagina_fim INTEGER
);

CREATE INDEX IF NOT EXISTS idx_doc_hash ON documentos(hash_sha256);
CREATE INDEX IF NOT EXISTS idx_doc_job ON documentos(ocr_job_id);
CREATE INDEX IF NOT EXISTS idx_ger_doc ON geracoes(documento_id);
CREATE INDEX IF NOT EXISTS idx_q_ger ON questoes(geracao_id);
"""


def _ensure_dir() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    _ensure_dir()
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


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
