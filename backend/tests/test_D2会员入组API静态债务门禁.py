from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TARGET = BACKEND_ROOT / "app" / "modules" / "member_enrollment" / "api.py"
RUFF_TARGET_RULES = {"E701", "E702", "F401", "F841", "I001", "UP017"}
EXPECTED_ROUTE_SIGNATURE_SHA256 = (
    "B16093E743AA0A114493AB7F6C08CE318A9B24D290D6F3B2CBD41A9233F88377"
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


def test_D2_1非B008静态债务必须归零且不得以抑制绕过() -> None:
    source = TARGET.read_text(encoding="utf-8")
    assert "# noqa" not in source, "D2_RUFF_SUPPRESSION_FORBIDDEN"
    counts = Counter(str(item["code"]) for item in _ruff_diagnostics())
    remaining = {code: counts[code] for code in sorted(RUFF_TARGET_RULES) if counts[code]}
    assert remaining == {}, f"D2_1_NON_B008_DEBT_REMAINS:{remaining}"


def test_D2_3全部依赖完成等价迁移且B008归零() -> None:
    counts = Counter(str(item["code"]) for item in _ruff_diagnostics())
    assert counts["B008"] == 0, "D2_3_B008_REMAINS"


def test_D2_1路由装饰器签名默认值与返回注解保持冻结() -> None:
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    route_signatures: list[tuple[object, ...]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.decorator_list:
            continue
        decorators = [
            ast.dump(item, annotate_fields=True, include_attributes=False)
            for item in node.decorator_list
        ]
        arguments = ast.dump(node.args, annotate_fields=True, include_attributes=False)
        returns = (
            ast.dump(node.returns, annotate_fields=True, include_attributes=False)
            if node.returns is not None
            else None
        )
        route_signatures.append((node.name, decorators, arguments, returns))
    payload = json.dumps(
        route_signatures,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(route_signatures) == 32, "D2_1_ROUTE_COUNT_DRIFT"
    assert hashlib.sha256(payload).hexdigest().upper() == EXPECTED_ROUTE_SIGNATURE_SHA256
