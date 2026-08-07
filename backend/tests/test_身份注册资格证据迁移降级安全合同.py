import importlib.util
from pathlib import Path

import pytest


REVISION_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "migrations"
    / "versions"
    / "20260807_0009_p1_registration_eligibility_evidence.py"
)


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, values):
        self.values = iter(values)

    def execute(self, statement):
        return _Scalar(next(self.values))


class _Op:
    def __init__(self, values):
        self.connection = _Connection(values)
        self.drops = []

    def get_bind(self):
        return self.connection

    def drop_table(self, name, schema):
        self.drops.append((schema, name))


def _load():
    spec = importlib.util.spec_from_file_location("eligibility_evidence_downgrade", REVISION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_资格证据任一表非空时downgrade零drop():
    module = _load()
    fake = _Op([True])
    module.op = fake
    with pytest.raises(RuntimeError, match="evidence tables are not empty"):
        module.downgrade()
    assert fake.drops == []


def test_资格证据全空时只按逆序drop三个表():
    module = _load()
    fake = _Op([False, False, False])
    module.op = fake
    module.downgrade()
    assert fake.drops == [
        ("public", "registration_eligibility_decision"),
        ("public", "user_account_classification_decision"),
        ("public", "identity_verification_decision"),
    ]
