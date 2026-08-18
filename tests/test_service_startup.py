"""Import every FastAPI application and assert its routes exist.

WHY THIS TEST EXISTS
--------------------
The unit suite was green while `docker compose up` crashed on startup. The
cause was a route decorator that only fails when FastAPI builds the route
object at import time:

    @router.delete("/{job_id}", status_code=204)
    async def delete_job(...) -> None:

Because that module uses `from __future__ import annotations`, the `-> None`
return annotation reaches FastAPI as the string "None", which resolves to
NoneType — a truthy class. FastAPI concludes the route has a response body and
asserts, since a 204 must not have one. No unit test touched `app.main`, so
nothing caught it until the container refused to boot.

Importing an app is the cheapest possible integration test, and it catches the
whole family of decorator-time errors: duplicate operation ids, bad response
models, unresolvable dependency annotations, syntax errors in any transitively
imported module.

Each service is imported in a SUBPROCESS. All three declare a top-level
package literally named `app`, so importing two of them into one interpreter
would resolve to whichever landed in sys.modules first and quietly test the
same service twice.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# A throwaway key. The API refuses to start without one, by design: shipping a
# default encryption key is how test defaults end up in production.
TEST_KEY = base64.urlsafe_b64encode(b"\x00" * 32).decode()

SERVICES = [
    pytest.param(
        "apps/api",
        [
            "/healthz",
            "/readyz",
            "/api/v1/jobs",
            "/api/v1/jobs/{job_id}",
            "/api/v1/templates",
            "/api/v1/documents",
        ],
        id="api",
    ),
    pytest.param("apps/ml-service", ["/healthz", "/readyz", "/extract"], id="ml-service"),
    pytest.param(
        "apps/converter",
        ["/healthz", "/doc-to-docx", "/docx-to-pdf"],
        id="converter",
    ),
]

PROBE = """
import json, sys
from app.main import app
print("ROUTES=" + json.dumps(sorted(
    r.path for r in app.routes if hasattr(r, "methods")
)))
"""


def _import_service(service_dir: str) -> list[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / service_dir)])
    env.setdefault("ENCRYPTION_KEY", TEST_KEY)
    env["ENVIRONMENT"] = "test"
    proc = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"{service_dir} failed to import:\n{proc.stderr[-4000:]}"
        )
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("ROUTES="))
    import json

    return json.loads(line[len("ROUTES=") :])


@pytest.mark.parametrize(("service_dir", "expected"), SERVICES)
def test_service_imports_and_exposes_routes(
    service_dir: str, expected: list[str]
) -> None:
    routes = _import_service(service_dir)
    missing = [path for path in expected if path not in routes]
    assert not missing, f"{service_dir} is missing routes {missing}; has {routes}"


def test_delete_job_returns_no_body() -> None:
    """A 204 route must declare no response model.

    Guards the specific regression above: if someone drops `response_model=None`
    the API stops importing entirely, and this asserts on the contract rather
    than only on the side effect.
    """
    source = (ROOT / "apps/api/app/routers/jobs.py").read_text(encoding="utf-8")
    assert 'status_code=204, response_model=None' in source, (
        "DELETE /jobs/{id} returns 204, so it must set response_model=None. "
        "With `from __future__ import annotations` a bare `-> None` makes "
        "FastAPI assert at import time."
    )
