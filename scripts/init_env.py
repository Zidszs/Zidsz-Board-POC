"""Gera `.env` a partir de `.env.example` sem gravar segredo no Git.

Uso (na raiz do repositório):
    python scripts/init_env.py

Se `.env` já existe, só preenche marcadores `__GENERATE_*__` e chaves que
faltam. Segredo já definido não é trocado (rotacionar a ENCRYPTION_KEY do
Langfuse ou do n8n inutiliza o banco).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from control_plane.envfile import ensure_env, parse_env, read_text  # noqa: E402


def main() -> int:
    example = _ROOT / ".env.example"
    dest = _ROOT / ".env"
    try:
        result = ensure_env(example, dest)
    except (OSError, ValueError, FileNotFoundError) as exc:
        print(f"Não foi possível preparar o .env: {exc}", file=sys.stderr)
        return 1
    print(result.message)
    if dest.exists():
        bruto = read_text(dest)
        if "NGROK_AUTHTOKEN" in bruto:
            token = parse_env(bruto).get("NGROK_AUTHTOKEN", "")
            if token in {"", "seu_token_do_ngrok_aqui", "your_ngrok_token"}:
                print("Falta o NGROK_AUTHTOKEN (pegue em https://dashboard.ngrok.com/get-started/your-authtoken).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
