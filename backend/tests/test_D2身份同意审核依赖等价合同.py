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
    "identity_submission",
    "identity_resubmit",
    "consent_presentations",
    "consent_record",
    "withdraw_consent",
    "revoke_proxy",
    "identity_reviews",
    "identity_review",
    "claim_review",
    "pii_access",
    "platform_decision",
    "create_document",
    "publish_document",
    "retire_document",
}
TARGET_ROUTES = {
    ("PUT", "/api/v1/family/member-enrollments/{enrollment_id}/identity-submission"),
    ("POST", "/api/v1/family/member-enrollments/{enrollment_id}/identity-resubmit"),
    ("GET", "/api/v1/family/member-enrollments/{enrollment_id}/consent-presentations"),
    ("POST", "/api/v1/family/member-enrollments/{enrollment_id}/consent-records"),
    ("POST", "/api/v1/family/consent-records/{consent_record_id}/withdraw"),
    ("POST", "/api/v1/family/proxy-grants/{grant_id}/revoke"),
    ("GET", "/api/v1/platform/member-identity-reviews"),
    ("GET", "/api/v1/platform/member-identity-reviews/{review_id}"),
    ("POST", "/api/v1/platform/member-identity-reviews/{review_id}/claim"),
    ("POST", "/api/v1/platform/member-identity-reviews/{review_id}/pii-access"),
    ("POST", "/api/v1/platform/member-identity-reviews/{review_id}/decision"),
    ("POST", "/api/v1/platform/consent-documents"),
    ("POST", "/api/v1/platform/consent-documents/{document_version_id}/publish"),
    ("POST", "/api/v1/platform/consent-documents/{document_version_id}/retire"),
}
EXPECTED_OPENAPI_SHA256 = (
    "6D9971E86DA2F257514D4EFFE8718692929751FCFE5725B3BB00471AD663335D"
)
EXPECTED_DEPENDENCY_GRAPH_SHA256 = (
    "2A468DBFA7E0647929C1C1F86F0FDBFB21A6B18DEED56F7DEFF4CDA55D17BDBE"
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


def test_D2_2目标53项B008归零且D2_3完成后全文件归零() -> None:
    source = TARGET.read_text(encoding="utf-8")
    assert "# noqa" not in source, "D2_RUFF_SUPPRESSION_FORBIDDEN"
    diagnostics = [item for item in _ruff_diagnostics() if item["code"] == "B008"]
    per_function = Counter(
        _function_for_row(int(item["location"]["row"]))  # type: ignore[index]
        for item in diagnostics
    )
    target_count = sum(per_function[name] for name in TARGET_FUNCTIONS)
    assert target_count == 0, "D2_2_TARGET_B008_REMAINS"
    assert sum(per_function.values()) == 0, "D2_3_B008_REMAINS"
    assert not (TARGET_FUNCTIONS & {name for name in per_function if name is not None})


def test_D2_2运行时依赖图与依赖覆盖callable保持等价() -> None:
    graph: dict[str, object] = {}
    for router in routers:
        for route in router.routes:
            methods = route.methods & {"GET", "POST", "PUT"}
            for method in sorted(methods):
                if (method, route.path) in TARGET_ROUTES:
                    graph[f"{method} {route.path}"] = [
                        _dependency_value(item) for item in route.dependant.dependencies
                    ]
    assert len(graph) == 14, "D2_2_RUNTIME_ROUTE_SET_DRIFT"
    assert _sha256(graph) == EXPECTED_DEPENDENCY_GRAPH_SHA256


def test_D2_2请求响应与OpenAPI子树保持等价() -> None:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    schema = strip_member_enrollment_validation_responses(app.openapi())
    subtree = {
        f"{method} {path}": schema["paths"][path][method.lower()]
        for method, path in sorted(TARGET_ROUTES)
    }
    assert _sha256(subtree) == EXPECTED_OPENAPI_SHA256
