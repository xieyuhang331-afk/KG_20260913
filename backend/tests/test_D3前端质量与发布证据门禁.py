from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "p2-foundation-ci.yml"
FRESH_GUIDE = REPOSITORY_ROOT / "docs" / "一期交付" / "一期Fresh环境复现说明_V1.md"
MANIFEST_TEMPLATE = REPOSITORY_ROOT / "docs" / "一期交付" / "一期发布证据Manifest模板_V1.md"

QUALITY_STEP_NAMES = (
    "Run frontend tests",
    "Run frontend lint",
    "Scan frontend source for PII",
    "Verify and scan frontend JUnit",
    "Scan frontend delivery artifacts",
    "Upload frontend test diagnostics",
    "Upload frontend build",
)
LINT_COMMANDS = (
    "npm run lint",
    "npm run lint:slice4",
    "npm run lint:slice5",
    "npm run lint:slice6",
    "npm run lint:slice7",
)


def _frontend_steps() -> list[dict[str, object]]:
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return payload["jobs"]["frontend-build"]["steps"]


def _step(name: str) -> dict[str, object] | None:
    return next((item for item in _frontend_steps() if item.get("name") == name), None)


def _run_text(name: str) -> str:
    item = _step(name)
    return "" if item is None else str(item.get("run", ""))


def test_D3_R01前端Vitest必须生成固定JUnit() -> None:
    command = _run_text("Run frontend tests")
    assert "npm run test" in command, "D3_FRONTEND_TEST_GATE_MISSING"
    assert "--reporter=junit" in command
    assert "--outputFile=frontend-test-report.xml" in command


def test_D3_R02前端Lint必须闭合base与Slice4至7() -> None:
    command = _run_text("Run frontend lint")
    assert command, "D3_FRONTEND_LINT_GATE_INCOMPLETE"
    for expected in LINT_COMMANDS:
        assert expected in command, "D3_FRONTEND_LINT_GATE_INCOMPLETE"


def test_D3_R03源码与交付产物PII门禁必须分离() -> None:
    assert "npm run pii:scan" in _run_text(
        "Scan frontend source for PII"
    ), "D3_FRONTEND_PII_GATE_MISSING"
    delivery_scan = _run_text("Scan frontend delivery artifacts")
    assert "frontend-test-report.xml" in delivery_scan, "D3_DELIVERY_SCAN_INCOMPLETE"
    assert '"dist"' in delivery_scan, "D3_DELIVERY_SCAN_INCOMPLETE"
    assert "一期发布证据Manifest模板_V1.md" in delivery_scan


def test_D3_R04JUnit诊断制品必须绑定Run且不盲传不安全报告() -> None:
    item = _step("Upload frontend test diagnostics")
    assert item is not None, "D3_FRONTEND_JUNIT_ARTIFACT_MISSING"
    condition = str(item.get("if", ""))
    values = item.get("with", {})
    assert "always()" in condition
    assert ".frontend-junit-safe" in condition
    assert values["name"] == "frontend-test-report-${{ github.run_id }}-${{ github.run_attempt }}"
    assert values["path"] == "frontend/frontend-test-report.xml"


def test_D3_R05Fresh复现说明必须存在且闭合() -> None:
    assert FRESH_GUIDE.exists(), "D3_FRESH_REPRODUCTION_CONTRACT_MISSING"
    source = FRESH_GUIDE.read_text(encoding="utf-8")
    for heading in (
        "## 冻结工具链与来源",
        "## Windows localhost",
        "## CI Linux",
        "## Fresh Disposable 服务",
        "## 证据与清理",
        "## 能力边界",
    ):
        assert heading in source, "D3_FRESH_REPRODUCTION_CONTRACT_MISSING"
    assert "CI-reproducible" in source
    assert "local deployment-ready" in source


def test_D3_R06ReleaseManifest模板必须存在且绑定完整证据() -> None:
    assert MANIFEST_TEMPLATE.exists(), "D3_RELEASE_MANIFEST_CONTRACT_MISSING"
    source = MANIFEST_TEMPLATE.read_text(encoding="utf-8")
    for token in (
        "CANDIDATE",
        "CI_VERIFIED",
        "DEPLOYABLE",
        "DEPLOYED",
        "APP_HANDOFF_READY",
        "Migration single Head",
        "OpenAPI SHA-256",
        "uv.lock SHA-256",
        "package-lock.json SHA-256",
        "JUnit",
        "资源清理",
        "延期风险",
    ):
        assert token in source, "D3_RELEASE_MANIFEST_CONTRACT_MISSING"


def test_D3_R07Dist只能在全部质量门禁后上传() -> None:
    names = [str(item.get("name", "")) for item in _frontend_steps()]
    try:
        indexes = {name: names.index(name) for name in QUALITY_STEP_NAMES}
    except ValueError:
        pytest.fail("D3_DIST_UPLOAD_ORDER_UNSAFE", pytrace=False)
    dist_index = indexes["Upload frontend build"]
    assert all(
        indexes[name] < dist_index for name in QUALITY_STEP_NAMES[:-1]
    ), "D3_DIST_UPLOAD_ORDER_UNSAFE"
    assert indexes["Run frontend tests"] < indexes["Verify and scan frontend JUnit"]
    assert indexes["Run frontend lint"] < indexes["Verify and scan frontend JUnit"]
    assert indexes["Scan frontend source for PII"] < indexes[
        "Verify and scan frontend JUnit"
    ]
    assert indexes["Verify and scan frontend JUnit"] < indexes[
        "Upload frontend test diagnostics"
    ]
    assert indexes["Scan frontend delivery artifacts"] < indexes[
        "Upload frontend test diagnostics"
    ]


def test_D3_V08不得引入吞错或缩小选择() -> None:
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][
        "frontend-build"
    ]
    assert "continue-on-error" not in str(job)
    for item in job["steps"]:
        command = str(item.get("run", ""))
        assert "|| true" not in command
        assert "--passWithNoTests" not in command


def test_D3_V09JUnitAlways仅保留安全诊断且不得改变失败结果() -> None:
    item = _step("Upload frontend test diagnostics")
    if item is None:
        return
    assert "always()" in str(item.get("if", ""))
    assert item.get("continue-on-error") is None
    assert item["with"]["path"] == "frontend/frontend-test-report.xml"


def _write_fake_npm(directory: Path) -> Path:
    program = directory / "fake_npm.py"
    program.write_text(
        "import os\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "target = 'ci' if args == ['ci'] else "
        "(args[1] if len(args) > 1 and args[0] == 'run' else 'invalid')\n"
        "raise SystemExit(23 if target == os.environ.get('D3_FAIL_TARGET') else 0)\n",
        encoding="utf-8",
    )
    return program


def _simulate_frontend_job(tmp_path: Path, fail_target: str) -> tuple[int, bool]:
    steps = _frontend_steps()
    required = {"test", "lint", "pii:scan"}
    observed: set[str] = set()
    program = _write_fake_npm(tmp_path)
    environment = os.environ.copy()
    environment["D3_FAIL_TARGET"] = fail_target
    npm_commands = [
        part.strip().split()
        for item in steps
        for part in str(item.get("run", "")).splitlines()
        if part.strip().startswith("npm ")
    ]
    available = {
        "ci" if arguments == ["npm", "ci"] else arguments[2]
        for arguments in npm_commands
    }
    assert required <= available, "D3_FRONTEND_FAILURE_FLOW_MISSING"
    exit_code = 0
    dist_uploaded = False
    for item in steps:
        name = str(item.get("name", ""))
        if name == "Upload frontend build":
            dist_uploaded = True
            break
        command = str(item.get("run", ""))
        for line in (part.strip() for part in command.splitlines()):
            if not line.startswith("npm "):
                continue
            arguments = line.split()
            target = "ci" if arguments == ["npm", "ci"] else arguments[2]
            observed.add(target)
            completed = subprocess.run(
                [sys.executable, str(program), *arguments[1:]],
                cwd=tmp_path,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                exit_code = completed.returncode
                break
        if exit_code != 0:
            break
    assert observed <= available
    return exit_code, dist_uploaded


@pytest.mark.parametrize("fail_target", ("test", "lint", "pii:scan"))
def test_D3离线合成失败传播使流程非零且Dist不可达(
    tmp_path: Path, fail_target: str
) -> None:
    exit_code, dist_uploaded = _simulate_frontend_job(tmp_path, fail_target)
    assert exit_code != 0, "D3_SYNTHETIC_FAILURE_NOT_PROPAGATED"
    assert not dist_uploaded, "D3_DIST_REACHED_AFTER_FAILURE"
