"""Gera docs/openapi.json a partir da aplicação (não precisa de banco).

Uso: python scripts/export_openapi.py [--check]
  --check  falha (exit 1) se o arquivo versionado estiver desatualizado (útil no CI).
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENVIRONMENT", "development")
os.environ["EXPOSE_DOCS"] = "true"

from app.main import app  # noqa: E402

TARGET = ROOT / "docs" / "openapi.json"


def main() -> int:
    content = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != content:
            print("docs/openapi.json desatualizado: rode python scripts/export_openapi.py")
            return 1
        print("docs/openapi.json atualizado")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(content, encoding="utf-8", newline="\n")
    print(f"OpenAPI {app.version} gravado em {TARGET.relative_to(ROOT)} ({len(app.openapi()['paths'])} caminhos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
