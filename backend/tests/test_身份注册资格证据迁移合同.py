import importlib.util
from pathlib import Path


REVISION_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "migrations"
    / "versions"
    / "20260807_0009_p1_registration_eligibility_evidence.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("eligibility_evidence_0009", REVISION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_注册资格证据迁移精确追加三个不可变事实表():
    module = _load()
    source = REVISION_PATH.read_text(encoding="utf-8")
    assert module.revision == "20260807_0009"
    assert module.down_revision == "20260806_0008"
    assert source.count("op.create_table(") == 3
    for table in (
        "identity_verification_decision",
        "user_account_classification_decision",
        "registration_eligibility_decision",
    ):
        assert f'"{table}"' in source
    assert "uq_registration_eligibility_canonical_key" in source
    assert "user_ref" in source
    assert "facts_version" in source
    assert "policy_version" in source
    assert "p1_projection_digest" in source, (
        "Eligibility migration must persist the P1 projection digest"
    )
    assert 'sa.String(length=64), nullable=False' in source
    assert (
        "registration_eligibility_p1_projection_digest_sha256" in source
    )
    assert "char_length(p1_projection_digest) = 64" in source
    for forbidden in (
        "phone",
        "id_card",
        "password",
        "CREATE SCHEMA",
        "IF NOT EXISTS",
        "op.execute",
    ):
        assert forbidden not in source


def test_supersedes单后继约束已冻结():
    source = REVISION_PATH.read_text(encoding="utf-8")
    expected_constraints = {
        "uq_identity_verification_single_successor",
        "uq_account_classification_single_successor",
    }
    missing = expected_constraints - {
        name for name in expected_constraints if name in source
    }
    assert not missing, (
        "Concurrent supersedes writers must produce exactly one successor"
    )
    assert source.count('"supersedes_ref"') >= 4
