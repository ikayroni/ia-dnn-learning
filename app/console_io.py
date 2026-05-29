"""Stdout/stderr UTF-8 no Windows (evita UnicodeEncodeError com setas, acentos, etc.)."""

from __future__ import annotations

import io
import sys

_CONFIGURED = False


def configure_stdio_utf8() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
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


def safe_print(msg: str, *, file=None) -> None:
    """Nunca propaga UnicodeEncodeError (comum no console Windows cp1252)."""
    configure_stdio_utf8()
    out = file or sys.stdout
    line = msg
    for attempt in (line, line.encode("ascii", errors="replace").decode("ascii")):
        try:
            print(attempt, flush=True, file=out)
            return
        except UnicodeEncodeError:
            continue
        except Exception:
            return
