from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextChunk:
    chunk_id: int
    text: str
    page_start: int | None = None
    page_end: int | None = None


def _split_paragraphs(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(stripped)
    if current:
        blocks.append("\n".join(current))
    return blocks if blocks else [text.strip()]


def chunk_text(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
    page_markers: list[tuple[int, int]] | None = None,
) -> list[TextChunk]:
    """
    Divide texto em trechos por parágrafos, respeitando tamanho máximo.
    page_markers: lista (offset_char, page_num) para rastrear páginas.
    """
    text = text.strip()
    if not text:
        return []

    if len(text) <= chunk_size:
        page = _page_at_offset(page_markers, 0) if page_markers else None
        return [TextChunk(chunk_id=0, text=text, page_start=page, page_end=page)]

    paragraphs = _split_paragraphs(text)
    chunks: list[TextChunk] = []
    buffer = ""
    char_offset = 0
    chunk_start_offset = 0
    chunk_id = 0

    def flush_buffer() -> None:
        nonlocal buffer, chunk_id, chunk_start_offset
        if not buffer.strip():
            return
        p_start = _page_at_offset(page_markers, chunk_start_offset) if page_markers else None
        p_end = _page_at_offset(page_markers, chunk_start_offset + len(buffer)) if page_markers else None
        chunks.append(
            TextChunk(
                chunk_id=chunk_id,
                text=buffer.strip(),
                page_start=p_start,
                page_end=p_end,
            )
        )
        chunk_id += 1
        if overlap > 0 and len(buffer) > overlap:
            buffer = buffer[-overlap:]
            chunk_start_offset = char_offset - len(buffer)
        else:
            buffer = ""
            chunk_start_offset = char_offset

    for para in paragraphs:
        candidate = f"{buffer}\n\n{para}".strip() if buffer else para
        if len(candidate) <= chunk_size:
            buffer = candidate
            char_offset += len(para) + 2
            continue

        if buffer:
            flush_buffer()

        while len(para) > chunk_size:
            piece = para[:chunk_size]
            p_start = _page_at_offset(page_markers, char_offset) if page_markers else None
            p_end = _page_at_offset(page_markers, char_offset + len(piece)) if page_markers else None
            chunks.append(
                TextChunk(chunk_id=chunk_id, text=piece, page_start=p_start, page_end=p_end)
            )
            chunk_id += 1
            char_offset += chunk_size - overlap if overlap else chunk_size
            para = para[chunk_size - overlap :] if overlap else para[chunk_size:]

        buffer = para
        chunk_start_offset = char_offset
        char_offset += len(para) + 2

    if buffer.strip():
        p_start = _page_at_offset(page_markers, chunk_start_offset) if page_markers else None
        p_end = _page_at_offset(page_markers, char_offset) if page_markers else None
        chunks.append(
            TextChunk(
                chunk_id=chunk_id,
                text=buffer.strip(),
                page_start=p_start,
                page_end=p_end,
            )
        )

    return chunks


def _page_at_offset(markers: list[tuple[int, int]], offset: int) -> int | None:
    page = None
    for char_pos, page_num in markers:
        if char_pos <= offset:
            page = page_num
        else:
            break
    return page


def _normalize(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def filter_chunks(
    chunks: list[TextChunk],
    *,
    keywords: list[str] | None = None,
    pagina_inicio: int | None = None,
    pagina_fim: int | None = None,
) -> tuple[list[TextChunk], dict]:
    """
    Filtra chunks por intervalo de páginas e palavras-chave.
    Ranqueia por número de ocorrências das keywords (mais matches primeiro).
    """
    info = {"input_chunks": len(chunks), "keywords": keywords or [], "pagina_inicio": pagina_inicio, "pagina_fim": pagina_fim}

    filtered = chunks
    if pagina_inicio or pagina_fim:
        pi = pagina_inicio or 0
        pf = pagina_fim or 10**9
        filtered = [
            c
            for c in filtered
            if (c.page_end or c.page_start or 0) >= pi
            and (c.page_start or c.page_end or 0) <= pf
        ]

    if keywords:
        normalized_kws = [_normalize(k) for k in keywords if k.strip()]
        scored: list[tuple[int, TextChunk]] = []
        for c in filtered:
            text_norm = _normalize(c.text)
            score = sum(text_norm.count(k) for k in normalized_kws)
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        filtered = [c for _, c in scored]

    info["output_chunks"] = len(filtered)
    return filtered, info
