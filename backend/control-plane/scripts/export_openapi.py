"""Write the API contract to openapi.json, or check that it is up to date.

    python scripts/export_openapi.py          # write openapi.json
    python scripts/export_openapi.py --check  # exit 1 if it is out of date

The file is committed, so every API change shows up in code review as a
diff of this file, and the dashboard (v2a) generates its TypeScript types
from it. tests/test_openapi_contract.py runs the check in the test suite.
"""

import json
import sys
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def render() -> str:
    """The current API as stable JSON: sorted keys, so the same API gives the same text."""
    from app.main import app  # imported here: the app needs settings from the environment

    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    current = render()
    if "--check" in argv:
        if not CONTRACT_PATH.exists() or CONTRACT_PATH.read_text(encoding="utf-8") != current:
            print("openapi.json is out of date. Run: python scripts/export_openapi.py")
            return 1
        print("openapi.json is up to date.")
        return 0
    CONTRACT_PATH.write_text(current, encoding="utf-8", newline="\n")
    print(f"Wrote {CONTRACT_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(CONTRACT_PATH.parent))  # so `app` is importable
    sys.exit(main(sys.argv[1:]))
