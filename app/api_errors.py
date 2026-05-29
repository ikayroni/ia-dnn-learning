"""Mapeia excecoes para respostas HTTP sem vazar erros de console Windows."""

from __future__ import annotations

from fastapi import HTTPException


def _is_console_encode_error(exc: BaseException) -> bool:
    if isinstance(exc, UnicodeEncodeError):
        return True
    msg = str(exc)
    return "charmap" in msg or "codec can't encode" in msg


def raise_http_for_exception(exc: BaseException) -> None:
    """Converte excecao em HTTPException (re-raise)."""
    if isinstance(exc, HTTPException):
        detail = exc.detail
        detail_str = detail if isinstance(detail, str) else str(detail)
        if _is_console_encode_error(exc) or "charmap" in detail_str:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Erro de codificacao no servidor. Feche todos os python run.py antigos "
                    "e inicie apenas: .venv\\Scripts\\python.exe run.py"
                ),
            ) from exc
        raise exc
    if _is_console_encode_error(exc):
        raise HTTPException(
            status_code=500,
            detail=(
                "Erro de codificacao no servidor (console Windows). "
                "Feche TODOS os terminais com python run.py e inicie de novo: "
                ".venv\\Scripts\\python.exe run.py"
            ),
        ) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, RuntimeError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc
