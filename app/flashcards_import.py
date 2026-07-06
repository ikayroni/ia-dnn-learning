"""Importação em lote de flashcards (CSV/TSV e TXT pipe)."""
from __future__ import annotations

import csv
import io
from typing import Any, Literal

FormatoImport = Literal["csv", "pipe"]
MAX_CARDS = 500


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def detect_format(content: str, filename: str = "") -> FormatoImport:
    ext = ""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "txt":
        return "pipe"
    if ext in ("csv", "tsv"):
        return "csv"

    sample = [ln.strip() for ln in (content or "").splitlines() if ln.strip()][:8]
    if not sample:
        return "csv"

    pipe_like = sum(1 for ln in sample if "|" in ln)
    if pipe_like >= max(1, int(len(sample) * 0.5)):
        return "pipe"
    return "csv"


def parse_flashcards_pipe(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cards: list[dict[str, Any]] = []
    rejeitados: list[dict[str, Any]] = []

    for i, line in enumerate((content or "").splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if "|" not in raw:
            rejeitados.append(
                {
                    "linha": i,
                    "motivo": "Linha sem separador | (use: pergunta|resposta)",
                    "conteudo": raw[:160],
                }
            )
            continue

        frente, _, verso = raw.partition("|")
        frente = frente.strip()
        verso = verso.strip()
        if not frente and not verso:
            continue
        if not frente:
            rejeitados.append({"linha": i, "motivo": "Frente vazia", "conteudo": raw[:160]})
            continue
        if not verso:
            rejeitados.append({"linha": i, "motivo": "Verso vazio", "conteudo": raw[:160]})
            continue
        cards.append({"frente": frente, "verso": verso})

    return cards, rejeitados


def parse_flashcards_csv(content: str) -> list[dict[str, Any]]:
    cards, _ = parse_flashcards_csv_with_report(content)
    return cards


def parse_flashcards_csv_with_report(
    content: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = (content or "").strip()
    if not text:
        raise ValueError("Arquivo vazio")

    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        raise ValueError("Nenhuma linha válida no arquivo")

    header = [c.strip().lower() for c in rows[0]]
    start = 0
    frente_idx = 0
    verso_idx = 1
    dica_idx: int | None = None
    tags_idx: int | None = None

    if header and header[0] in ("frente", "front", "pergunta", "question"):
        start = 1
        for i, h in enumerate(header):
            if h in ("frente", "front", "pergunta", "question"):
                frente_idx = i
            elif h in ("verso", "back", "resposta", "answer"):
                verso_idx = i
            elif h in ("dica", "hint"):
                dica_idx = i
            elif h in ("tags", "tag", "etiquetas"):
                tags_idx = i

    cards: list[dict[str, Any]] = []
    rejeitados: list[dict[str, Any]] = []

    for offset, row in enumerate(rows[start:], start=start + 1):
        if len(row) <= max(frente_idx, verso_idx):
            rejeitados.append(
                {
                    "linha": offset,
                    "motivo": "Colunas insuficientes",
                    "conteudo": delimiter.join(row)[:160],
                }
            )
            continue
        frente = (row[frente_idx] if frente_idx < len(row) else "").strip()
        verso = (row[verso_idx] if verso_idx < len(row) else "").strip()
        if not frente and not verso:
            continue
        if not frente or not verso:
            rejeitados.append(
                {
                    "linha": offset,
                    "motivo": "Frente ou verso vazio",
                    "conteudo": delimiter.join(row)[:160],
                }
            )
            continue
        card: dict[str, Any] = {"frente": frente, "verso": verso}
        if dica_idx is not None and dica_idx < len(row):
            dica = row[dica_idx].strip()
            if dica:
                card["dica"] = dica
        if tags_idx is not None and tags_idx < len(row):
            raw_tags = row[tags_idx].strip()
            if raw_tags:
                card["tags"] = [t.strip() for t in raw_tags.split(";") if t.strip()]
        cards.append(card)

    if not cards and not rejeitados:
        raise ValueError("Nenhum card válido encontrado. Use colunas frente,verso")
    return cards, rejeitados


def parse_flashcards_with_report(content: str, filename: str = "") -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValueError("Arquivo vazio")

    formato = detect_format(text, filename)
    total_linhas = sum(1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#"))

    if formato == "pipe":
        cards, rejeitados = parse_flashcards_pipe(text)
    else:
        cards, rejeitados = parse_flashcards_csv_with_report(text)

    if not cards:
        raise ValueError(
            "Nenhum card válido encontrado. "
            "TXT: uma linha por card no formato pergunta|resposta. "
            "CSV: colunas frente,verso."
        )

    limite_excedido = len(cards) > MAX_CARDS
    if limite_excedido:
        raise ValueError(f"Máximo de {MAX_CARDS} cards por importação (encontrados {len(cards)})")

    return {
        "formato": formato,
        "total_linhas": total_linhas,
        "validos": len(cards),
        "invalidos": len(rejeitados),
        "cards": cards,
        "amostra_validos": [{"frente": c["frente"], "verso": c["verso"]} for c in cards[:25]],
        "rejeitados": rejeitados[:50],
    }


def decode_import_file(raw: bytes) -> str:
    return _decode_bytes(raw)
