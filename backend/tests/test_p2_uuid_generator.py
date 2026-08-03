import ast
import inspect
import uuid
from pathlib import Path

import pytest

from app.core import uuid_generator


def _new_generator():
    adapter = getattr(uuid_generator, "Uuid7Generator", None)
    assert adapter is not None, "Uuid7Generator adapter is not implemented"
    return adapter()


def _generate() -> uuid.UUID:
    return _new_generator().generate()


def test_generator_protocol_accepts_no_business_inputs():
    assert list(inspect.signature(uuid_generator.UuidGenerator.generate).parameters) == [
        "self"
    ]


def test_adapter_returns_exact_standard_uuid_type():
    assert type(_generate()) is uuid.UUID


def test_adapter_returns_uuid_version_7():
    assert _generate().version == 7


def test_adapter_returns_rfc_variant():
    assert _generate().variant == uuid.RFC_4122


def test_adapter_returns_canonical_uuid():
    generated = _generate()

    assert str(uuid.UUID(str(generated))) == str(generated)
    assert str(generated) == str(generated).lower()
    assert len(str(generated)) == 36


def test_adapter_uuid_round_trips_through_bytes_and_int():
    generated = _generate()

    assert uuid.UUID(bytes=generated.bytes) == generated
    assert uuid.UUID(int=generated.int) == generated


def test_adapter_rejects_business_seed_input():
    generator = _new_generator()

    with pytest.raises(TypeError):
        generator.generate(seed="tenant-or-user-value")


def test_adapter_source_contains_no_uuid_downgrade():
    source = Path(uuid_generator.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"uuid1", "uuid3", "uuid4", "uuid5"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "uuid":
            assert forbidden.isdisjoint(alias.name for alias in node.names)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert not (node.value.id == "uuid" and node.attr in forbidden)


def test_third_party_uuid_library_is_confined_to_adapter_module():
    backend_root = Path(__file__).resolve().parents[1]
    adapter_path = Path(uuid_generator.__file__).resolve()
    imports = []

    for path in (backend_root / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(module == "uuid_utils" or module.startswith("uuid_utils.") for module in modules):
                imports.append(path.resolve())

    assert all(path == adapter_path for path in imports)
