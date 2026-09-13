from __future__ import annotations

import ast
import hashlib
import inspect
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from fastapi import FastAPI

from app.modules.member_enrollment.api import (
    routers,
    strip_member_enrollment_validation_responses,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TARGET = BACKEND_ROOT / "app" / "modules" / "member_enrollment" / "api.py"
TARGET_FUNCTIONS = {
    "accept_assignment",
    "accept_enrollment",
    "cancel_assignment",
    "create_assignment",
    "create_invitation",
    "decline_assignment",
    "family_enrollment",
    "family_enrollments",
    "get_primary_therapist_assignment",
    "institution_case",
    "institution_enrollment",
    "institution_enrollments",
    "institution_identity_check",
    "list_invitations",
    "resend_invitation",
    "revoke_invitation",
    "therapist_assignments",
    "therapist_case",
}
TARGET_ROUTES = {
    ("POST", "/api/v1/institution/member-invitations"),
    ("GET", "/api/v1/institution/member-invitations"),
    ("POST", "/api/v1/institution/member-invitations/{invitation_id}/resend"),
    ("POST", "/api/v1/institution/member-invitations/{invitation_id}/revoke"),
    ("GET", "/api/v1/institution/member-enrollments"),
    ("GET", "/api/v1/institution/member-enrollments/{enrollment_id}"),
    ("POST", "/api/v1/institution/member-enrollments/{enrollment_id}/identity-check"),
    ("POST", "/api/v1/family/member-enrollments/accept"),
    ("GET", "/api/v1/family/member-enrollments"),
    ("GET", "/api/v1/family/member-enrollments/{enrollment_id}"),
    ("POST", "/api/v1/institution/member-enrollments/{enrollment_id}/primary-assignments"),
    ("POST", "/api/v1/institution/primary-assignments/{assignment_id}/cancel"),
    ("GET", "/api/v1/institution/service-cases/{case_id}"),
    ("GET", "/api/v1/therapist/primary-assignments"),
    ("GET", "/api/v1/therapist/primary-assignments/{assignment_id}"),
    ("POST", "/api/v1/therapist/primary-assignments/{assignment_id}/accept"),
    ("POST", "/api/v1/therapist/primary-assignments/{assignment_id}/decline"),
    ("GET", "/api/v1/therapist/service-cases/{case_id}"),
}
EXPECTED_OPENAPI_SHA256 = (
    "E55DCFB59DF313A79824CF04527872725293AC5A3D800D8ACC507AC314B08063"
)
EXPECTED_DEPENDENCY_GRAPH_SHA256 = (
    "BE0620CB072C1BC22397FBDFDF794C15C5323F7F77E80450322C0D07D90D389F"
)


def _ruff_diagnostics() -> list[dict[str, object]]:
    executable = shutil.which("ruff")
    assert executable is not None, "RUFF_EXECUTABLE_MISSING"
    completed = subprocess.run(
        [
            executable,
            "check",
            str(TARGET.relative_to(BACKEND_ROOT)),
            "--output-format",
            "json",
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )
    assert completed.returncode in {0, 1}, "D2_RUFF_EXECUTION_FAILED"
    return json.loads(completed.stdout)


def _function_for_row(row: int) -> str | None:
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.lineno <= row <= (node.end_lineno or node.lineno)
        ):
            return node.name
    return None


def _call_name(call: object) -> str:
    if inspect.isfunction(call):
        return f"{call.__module__}.{call.__qualname__}"
    return f"{type(call).__module__}.{type(call).__qualname__}"


def _dependency_value(dependant: object) -> dict[str, object]:
    dependencies = dependant.dependencies
    try:
        security_scopes = dependant.security_scopes or []
    except AttributeError:
        security_scopes = []
    return {
        "name": dependant.name,
        "call": _call_name(dependant.call),
        "use_cache": dependant.use_cache,
        "scope": dependant.scope,
        "security_scopes": sorted(security_scopes),
        "children": [_dependency_value(item) for item in dependencies],
    }


def _sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def test_D2_3目标18端点50项B008必须归零() -> None:
    source = TARGET.read_text(encoding="utf-8")
    assert "# noqa" not in source, "D2_RUFF_SUPPRESSION_FORBIDDEN"
    diagnostics = [item for item in _ruff_diagnostics() if item["code"] == "B008"]
    per_function = Counter(
        _function_for_row(int(item["location"]["row"]))  # type: ignore[index]
        for item in diagnostics
    )
    target_count = sum(per_function[name] for name in TARGET_FUNCTIONS)
    assert target_count == 0, "D2_3_TARGET_B008_REMAINS"
    assert sum(per_function.values()) == 0, "D2_3_UNSCOPED_B008_REMAINS"


def test_D2_3递归依赖图与override_callable保持等价() -> None:
    graph: dict[str, object] = {}
    for router in routers:
        for route in router.routes:
            methods = route.methods & {"GET", "POST", "PUT"}
            for method in sorted(methods):
                if (method, route.path) in TARGET_ROUTES:
                    graph[f"{method} {route.path}"] = [
                        _dependency_value(item) for item in route.dependant.dependencies
                    ]
    assert len(graph) == 18, "D2_3_RUNTIME_ROUTE_SET_DRIFT"
    assert _sha256(graph) == EXPECTED_DEPENDENCY_GRAPH_SHA256


def test_D2_3请求查询响应安全与OpenAPI子树保持等价() -> None:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    schema = strip_member_enrollment_validation_responses(app.openapi())
    subtree = {
        f"{method} {path}": schema["paths"][path][method.lower()]
        for method, path in sorted(TARGET_ROUTES)
    }
    assert _sha256(subtree) == EXPECTED_OPENAPI_SHA256
