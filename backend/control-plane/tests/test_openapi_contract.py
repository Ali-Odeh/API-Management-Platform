"""The committed openapi.json must match the running API.

If this fails, you changed the API on purpose (or by accident!). Look at the
diff, then refresh the contract:

    docker compose exec control-plane python scripts/export_openapi.py
"""

from scripts.export_openapi import CONTRACT_PATH, render


def test_openapi_contract_is_up_to_date() -> None:
    assert CONTRACT_PATH.exists(), "run scripts/export_openapi.py to create openapi.json"
    assert CONTRACT_PATH.read_text(encoding="utf-8") == render(), (
        "The API changed but openapi.json didn't. "
        "Run: docker compose exec control-plane python scripts/export_openapi.py"
    )
