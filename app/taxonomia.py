from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.config import BASE_DIR

TAXONOMIA_PATH = BASE_DIR / "app" / "resources" / "taxonomia_temas.json"

Taxonomia = dict[str, dict[str, list[str]]]


@lru_cache(maxsize=1)
def carregar_taxonomia() -> Taxonomia:
    """Carrega a taxonomia fixa de disciplina -> tema -> subtemas (base: TEMAS.docx).

    Estrutura: {"CLINICA MEDICA": {"Cardiologia": ["valvulopatie", ...], ...}, ...}
    """
    try:
        raw = TAXONOMIA_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        return {}
    return data


def disciplinas_disponiveis() -> list[str]:
    return list(carregar_taxonomia().keys())


def temas_da_disciplina(disciplina: str | None) -> dict[str, list[str]]:
    """Retorna {tema: [subtemas]} de uma disciplina específica, ou {} se não encontrada."""
    if not disciplina:
        return {}
    taxonomia = carregar_taxonomia()
    return taxonomia.get(disciplina.strip().upper(), {})
