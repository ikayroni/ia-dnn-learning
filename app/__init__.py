"""Pacote app — UTF-8 no console antes de qualquer import que faca log."""

from __future__ import annotations

import builtins
import sys

_BUILD = "2026-05-29-utf8-v2"


def _patch_builtin_print() -> None:
    if getattr(builtins.print, "_llm_safe", False):
        return
    _orig = builtins.print

    def _safe_print(*args, **kwargs):
        try:
            _orig(*args, **kwargs)
        except UnicodeEncodeError:
            safe_args = []
            for a in args:
                if isinstance(a, str):
                    safe_args.append(a.encode("ascii", errors="replace").decode("ascii"))
                else:
                    safe_args.append(a)
            _orig(*safe_args, **kwargs)

    _safe_print._llm_safe = True  # type: ignore[attr-defined]
    builtins.print = _safe_print


def _patch_stdio() -> None:
    import io

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            if hasattr(stream, "buffer"):
                setattr(
                    sys,
                    name,
                    io.TextIOWrapper(
                        stream.buffer,
                        encoding="utf-8",
                        errors="replace",
                        line_buffering=True,
                    ),
                )
            elif hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_patch_stdio()
_patch_builtin_print()
