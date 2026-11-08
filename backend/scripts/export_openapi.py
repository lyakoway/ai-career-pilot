"""Детерминированный экспорт OpenAPI-схемы приложения.

Использование: uv run python scripts/export_openapi.py <output.json>
(sort_keys + фиксированный indent — генерация байт-в-байт воспроизводима).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import create_app


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: export_openapi.py <output-path>")
    schema = create_app().openapi()
    out = Path(sys.argv[1])
    out.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
