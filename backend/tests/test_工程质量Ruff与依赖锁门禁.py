from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
import unicodedata
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
PYPROJECT_PATH = BACKEND_ROOT / "pyproject.toml"
LOCK_PATH = BACKEND_ROOT / "uv.lock"
BASELINE_PATH = BACKEND_ROOT / "ruff基线_V1.json"
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "p2-foundation-ci.yml"

RUFF_VERSION = "0.16.5"
RUFF_TARGET = "py311"
RUFF_SELECT = ["E4", "E7", "E9", "F", "I", "UP", "B", "SIM", "ASYNC"]
P0_CODES = {"E9", "F821"}
EVIDENCE_FIELDS = {
    "commit_sha",
    "python_exact_version",
    "python_cache_tag",
    "uv_version",
    "ruff_version",
    "pyproject_sha256",
    "uv_lock_sha256",
    "resolution_graph_sha256",
    "ruff_baseline_sha256",
    "runner_os",
    "runner_image",
    "runner_image_version",
    "uv_lock_check_exit",
    "uv_sync_frozen_exit",
    "ruff_gate_exit",
    "pytest_exit",
}


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _pyproject() -> dict[str, object]:
    with PYPROJECT_PATH.open("rb") as file:
        return tomllib.load(file)


def _baseline() -> dict[str, object]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _normalized_message(message: str) -> str:
    normalized = unicodedata.normalize("NFC", message.replace("\r\n", "\n")).strip()
    for prefix in (str(REPOSITORY_ROOT.resolve()), str(BACKEND_ROOT.resolve())):
        normalized = normalized.replace(prefix, "<repo>")
    return normalized


def _normalized_path(filename: str) -> str:
    path = Path(filename)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    relative = path.resolve().relative_to(BACKEND_ROOT.resolve())
    return unicodedata.normalize("NFC", relative.as_posix())


def _key(entry: dict[str, object]) -> tuple[str, str, str]:
    return (str(entry["path"]), str(entry["code"]), str(entry["message"]))


def _byte_sort_key(entry: dict[str, object]) -> tuple[bytes, bytes, bytes]:
    return tuple(part.encode("utf-8") for part in _key(entry))  # type: ignore[return-value]


def _baseline_counter(document: dict[str, object]) -> Counter[tuple[str, str, str]]:
    result: Counter[tuple[str, str, str]] = Counter()
    for entry in document["entries"]:  # type: ignore[index]
        result[_key(entry)] += int(entry["count"])  # type: ignore[index]
    return result


def _debt_regressions(
    current: Counter[tuple[str, str, str]],
    baseline: Counter[tuple[str, str, str]],
) -> list[tuple[tuple[str, str, str], int, int]]:
    return sorted(
        (
            (key, count, baseline.get(key, 0))
            for key, count in current.items()
            if count > baseline.get(key, 0)
        ),
        key=lambda item: tuple(part.encode("utf-8") for part in item[0]),
    )


def _current_ruff_counter() -> Counter[tuple[str, str, str]]:
    executable = shutil.which("ruff")
    assert executable is not None, "RUFF_EXECUTABLE_MISSING"
    completed = subprocess.run(
        [
            executable,
            "check",
            "app",
            "tests",
            "--select",
            ",".join(RUFF_SELECT),
            "--output-format",
            "json",
            "--exit-zero",
        ],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result: Counter[tuple[str, str, str]] = Counter()
    for diagnostic in json.loads(completed.stdout):
        result[
            (
                _normalized_path(diagnostic["filename"]),
                str(diagnostic["code"]),
                _normalized_message(diagnostic["message"]),
            )
        ] += 1
    return result


def test_R01_缺少uv_lock时拒绝() -> None:
    assert LOCK_PATH.is_file(), "UV_LOCK_MISSING"


def test_R02_CI未使用frozen_sync时拒绝() -> None:
    workflow = _workflow_text()
    assert workflow.count("uv lock --check") >= 2, "CI_UV_FROZEN_SYNC_MISSING"
    assert (
        workflow.count("uv sync --frozen --all-groups --python 3.11.16") >= 2
    ), "CI_UV_FROZEN_SYNC_MISSING"


def test_R03_Ruff工具与规则必须精确冻结() -> None:
    project = _pyproject()
    dev = project.get("dependency-groups", {}).get("dev", [])  # type: ignore[union-attr]
    ruff = project.get("tool", {}).get("ruff", {})  # type: ignore[union-attr]
    lint = ruff.get("lint", {}) if isinstance(ruff, dict) else {}
    assert f"ruff=={RUFF_VERSION}" in dev, "RUFF_CONTRACT_MISSING"
    assert ruff.get("target-version") == RUFF_TARGET, "RUFF_CONTRACT_MISSING"
    assert lint.get("select") == RUFF_SELECT, "RUFF_CONTRACT_MISSING"


def test_R04_E9_F821全局零门禁必须存在() -> None:
    workflow = _workflow_text()
    assert (
        "uv run --frozen ruff check app tests --select E9,F821" in workflow
    ), "RUFF_P0_GATE_MISSING"


def test_R05_normalized_baseline必须存在且schema合法() -> None:
    assert BASELINE_PATH.is_file(), "RUFF_BASELINE_MISSING"
    document = _baseline()
    assert set(document) == {
        "schema_version",
        "ruff_version",
        "target_version",
        "select",
        "source_commit",
        "entries",
        "total",
    }, "RUFF_BASELINE_SCHEMA_INVALID"
    assert document["schema_version"] == 1, "RUFF_BASELINE_SCHEMA_INVALID"
    assert document["ruff_version"] == RUFF_VERSION, "RUFF_BASELINE_SCHEMA_INVALID"
    assert document["target_version"] == RUFF_TARGET, "RUFF_BASELINE_SCHEMA_INVALID"
    assert document["select"] == RUFF_SELECT, "RUFF_BASELINE_SCHEMA_INVALID"
    assert document["source_commit"] == "9a3a9843c6f250849614dfbac9c2ee0146ad9d57", (
        "RUFF_BASELINE_SOURCE_INVALID"
    )
    entries = document["entries"]
    assert isinstance(entries, list), "RUFF_BASELINE_SCHEMA_INVALID"
    assert all(set(entry) == {"path", "code", "message", "count"} for entry in entries), (
        "RUFF_BASELINE_SCHEMA_INVALID"
    )
    assert all(entry["code"] not in P0_CODES and int(entry["count"]) > 0 for entry in entries), (
        "RUFF_BASELINE_SCHEMA_INVALID"
    )
    assert entries == sorted(entries, key=_byte_sort_key), "RUFF_BASELINE_ORDER_INVALID"
    assert len({_key(entry) for entry in entries}) == len(entries), "RUFF_BASELINE_DUPLICATE_KEY"
    assert document["total"] == sum(int(entry["count"]) for entry in entries), (
        "RUFF_BASELINE_TOTAL_INVALID"
    )


def test_R06_changed_file新债必须被拒绝() -> None:
    existing = ("app/example.py", "F401", "unused import")
    new_key = ("app/example.py", "B008", "new call in default argument")
    baseline = Counter({existing: 1})
    regressions = _debt_regressions(Counter({existing: 2, new_key: 1}), baseline)
    assert regressions == [(new_key, 1, 0), (existing, 2, 1)]
    assert "test_工程质量Ruff与依赖锁门禁.py" in _workflow_text(), (
        "CHANGED_FILE_DEBT_GATE_MISSING"
    )


def test_R07_Fresh工具链证据清单必须完整() -> None:
    workflow = _workflow_text()
    missing = sorted(field for field in EVIDENCE_FIELDS if f'"{field}":' not in workflow)
    assert not missing, "D1_EVIDENCE_CONTRACT_MISSING"
    assert 'f"{name}={value}\\n"' in workflow, "D1_EVIDENCE_CONTRACT_MISSING"


def test_normalized_baseline与当前诊断一致() -> None:
    current = _current_ruff_counter()
    p0 = sorted((key, count) for key, count in current.items() if key[1] in P0_CODES)
    assert not p0, f"RUFF_P0_DEBT_PRESENT: {p0[:5]}"
    current_without_p0 = Counter(
        {key: count for key, count in current.items() if key[1] not in P0_CODES}
    )
    regressions = _debt_regressions(current_without_p0, _baseline_counter(_baseline()))
    assert not regressions, f"RUFF_NEW_DEBT: {regressions[:5]}"
