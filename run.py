"""Sobe a API. No Windows, reload desligado para evitar worker com codigo antigo."""

import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8003"))
    # reload=True costuma deixar worker antigo na porta no Windows
    use_reload = os.environ.get("RELOAD", "").lower() in ("1", "true", "yes")
    if sys.platform == "win32" and "RELOAD" not in os.environ:
        use_reload = False

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=use_reload,
    )
