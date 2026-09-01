from __future__ import annotations

import importlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

CLASS_NAMES = tuple(f"H{index}" for index in range(8))


def _inventory_module():
    return importlib.import_module("app.modules.auth.identity_remediation")


def _facts(**overrides):
    module = _inventory_module()
    defaults = {
        "role": "member",
        "legacy_pii_present": False,
        "identity_authority_signal": False,
        "formal_chain_complete": False,
        "tenant_present": False,
        "tenant_relation_known": True,
        "self_link_count": 0,
        "enrollment_count": 0,
        "current_enrollment_count": 0,
        "tenant_matches_unique_current": False,
        "enrollment_scope_complete": False,
    }
    defaults.update(overrides)
    return module.IdentitySnapshotFacts(**defaults)


def _sample_by_class():
    return {
        "H0": _facts(),
        "H1": _facts(legacy_pii_present=True),
        "H2": _facts(identity_authority_signal=True, formal_chain_complete=False),
        "H3": _facts(
            legacy_pii_present=True,
            identity_authority_signal=True,
            formal_chain_complete=True,
        ),
        "H4": _facts(
            tenant_present=True,
            self_link_count=1,
            enrollment_count=0,
        ),
        "H5": _facts(
            tenant_present=True,
            self_link_count=1,
            enrollment_count=1,
            current_enrollment_count=1,
            tenant_matches_unique_current=True,
            enrollment_scope_complete=True,
        ),
        "H6": _facts(
            tenant_present=True,
            self_link_count=0,
            enrollment_count=0,
        ),
        "H7": _facts(role="org_admin"),
    }


def test_A2_R31_R32_H0_H7主分类互斥穷尽且计数守恒() -> None:
    module = _inventory_module()
    samples = _sample_by_class()

    assert {name: module.classify_snapshot(facts).primary_class for name, facts in samples.items()} == {
        name: name for name in CLASS_NAMES
    }

    report = module.build_inventory_report(samples.values())
    assert report.input_count == 8
    assert report.class_counts == {name: 1 for name in CLASS_NAMES}
    assert report.input_count == sum(report.class_counts.values())
    assert report.multi_primary_match_count == 0
    assert report.unclassified_count == 0
    assert report.status == "PASS"


def test_A2_R31_冲突样本只取冻结优先级并仅输出匿名次级标签() -> None:
    module = _inventory_module()
    samples = (
        _facts(
            role="org_admin",
            legacy_pii_present=True,
            identity_authority_signal=True,
            formal_chain_complete=True,
            tenant_present=True,
            tenant_relation_known=False,
        ),
        _facts(
            legacy_pii_present=True,
            identity_authority_signal=True,
            formal_chain_complete=False,
            tenant_present=True,
            tenant_relation_known=False,
        ),
        _facts(
            legacy_pii_present=True,
            identity_authority_signal=True,
            formal_chain_complete=True,
            tenant_present=True,
            self_link_count=0,
        ),
        _facts(
            legacy_pii_present=True,
            tenant_present=True,
            self_link_count=0,
        ),
    )

    results = [module.classify_snapshot(sample) for sample in samples]
    assert [result.primary_class for result in results] == ["H7", "H2", "H3", "H1"]
    assert all(result.secondary_labels for result in results)
    assert all(
        set(result.secondary_labels) <= module.ALLOWED_SECONDARY_LABELS
        for result in results
    )


def test_A2_R32_无旧PII且无tenant的完整formal事实仍归H0() -> None:
    module = _inventory_module()
    facts = _facts(
        identity_authority_signal=True,
        formal_chain_complete=True,
    )

    assert module.classify_snapshot(facts).primary_class == "H0"


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            lambda: _facts(
                tenant_present=True,
                self_link_count=1,
                enrollment_count=2,
                current_enrollment_count=1,
                tenant_matches_unique_current=True,
                enrollment_scope_complete=True,
            ),
            "H5",
        ),
        (
            lambda: _facts(
                tenant_present=True,
                self_link_count=1,
                enrollment_count=1,
                current_enrollment_count=0,
                tenant_matches_unique_current=False,
                enrollment_scope_complete=False,
            ),
            "H6",
        ),
    ],
)
def test_A2_R32_R33_历史enrollment不得破坏current唯一性(facts, expected) -> None:
    module = _inventory_module()
    assert module.classify_snapshot(facts()).primary_class == expected


@pytest.mark.parametrize(
    "facts",
    [
        lambda: _facts(identity_authority_signal=None),
        lambda: _facts(formal_chain_complete=None),
    ],
)
def test_A2_R33_身份unknown必须归H2(facts) -> None:
    module = _inventory_module()
    assert module.classify_snapshot(facts()).primary_class == "H2"


@pytest.mark.parametrize(
    "facts",
    [
        lambda: _facts(tenant_present=None),
        lambda: _facts(tenant_relation_known=False),
        lambda: _facts(tenant_present=True, self_link_count=None),
        lambda: _facts(tenant_present=True, enrollment_count=None),
    ],
)
def test_A2_R33_机构关系unknown必须归H6且不得归H0_H4_H5(facts) -> None:
    module = _inventory_module()
    assert module.classify_snapshot(facts()).primary_class == "H6"


def test_A2_R34_公开Evidence闭合脱敏且相同快照Hash稳定() -> None:
    module = _inventory_module()
    samples = tuple(_sample_by_class().values())
    first = module.build_inventory_report(samples)
    second = module.build_inventory_report(tuple(reversed(samples)))

    first_document = first.to_public_dict()
    second_document = second.to_public_dict()
    assert first.evidence_hash == second.evidence_hash
    assert first_document["classification_rule_hash"] == second_document[
        "classification_rule_hash"
    ]
    assert first_document["code_sha256"] == second_document["code_sha256"]
    assert first_document["batch_ref"] != second_document["batch_ref"]
    assert first_document["snapshot_at"]
    assert second_document["snapshot_at"]
    assert first_document["snapshot_ceiling_present"] is False
    assert first_document["anonymous_watermark_present"] is False
    assert first_document["migration_head"] == "20260901_0034"
    assert first_document["total_count"] == 8
    assert first_document["member_count"] == 7
    assert first_document["excluded_non_member_count"] == 1
    assert first_document["processed_count"] == 8
    assert first_document["page_count"] == 1
    assert first_document["batch_status"] == "COMPLETED"
    assert first_document["small_count_present"] is True
    assert first_document["class_counts"] == {
        name: "SMALL_COUNT" for name in CLASS_NAMES
    }
    assert set(first_document["secondary_label_counts"]) == set(
        module.ALLOWED_SECONDARY_LABELS
    )
    assert all(
        value in {0, "SMALL_COUNT"}
        for value in first_document["secondary_label_counts"].values()
    )
    assert set(first_document) == module.PUBLIC_REPORT_KEYS
    assert not module.find_sensitive_output_keys(first_document)
    serialized = json.dumps(first_document, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "user_id",
        "user_ref",
        "member_id",
        "tenant_id",
        "real_name",
        "id_card",
        "ciphertext",
        "digest",
        "fingerprint",
        "key_id",
    ):
        assert forbidden not in serialized.lower()


def test_A2_R31_R32_矛盾事实或计数不闭合必须fail_closed() -> None:
    module = _inventory_module()
    contradictory = _facts(
        identity_authority_signal=False,
        formal_chain_complete=True,
    )
    with pytest.raises(module.InventoryContractError, match="INVENTORY_FACTS_INCONSISTENT"):
        module.classify_snapshot(contradictory)

    good = module.build_inventory_report(_sample_by_class().values())
    broken = replace(good, input_count=good.input_count + 1)
    with pytest.raises(module.InventoryContractError, match="INVENTORY_COUNT_MISMATCH"):
        module.validate_inventory_report(broken)

    inconsistent_reports = (
        replace(good, member_count=good.member_count + 1),
        replace(good, excluded_non_member_count=good.excluded_non_member_count + 1),
        replace(good, processed_count=0),
        replace(good, page_count=99),
        replace(good, batch_status="RUNNING"),
        replace(good, small_count_present=False),
        replace(good, evidence_hash="BROKEN"),
    )
    for inconsistent in inconsistent_reports:
        with pytest.raises(
            module.InventoryContractError,
            match="INVENTORY_REPORT_INCONSISTENT",
        ):
            module.validate_inventory_report(inconsistent)


def test_A2_R34_CLI禁止数据库URL与行级筛选且Repository只读(
    monkeypatch, capsys
) -> None:
    cli = importlib.import_module("scripts.run_a2_identity_inventory")
    repository_path = Path("app/modules/auth/identity_remediation_repository.py")
    composition_path = Path("app/composition/identity_remediation.py")

    forbidden_argument_sets = (
        ["--database-url", "credentialed-value"],
        ["--user-id", "credentialed-value"],
        ["--dsn", "credentialed-value"],
        ["-d", "credentialed-value"],
        ["--format", "credentialed-value"],
        ["credentialed-value"],
    )
    for arguments in forbidden_argument_sets:
        with pytest.raises(SystemExit):
            cli.parse_args(arguments)
        captured = capsys.readouterr()
        assert "credentialed-value" not in captured.out
        assert "credentialed-value" not in captured.err

    monkeypatch.setattr(
        "sys.argv",
        ["run_a2_identity_inventory.py", "--dsn", "credentialed-value"],
    )
    with pytest.raises(SystemExit):
        cli.parse_args()
    captured = capsys.readouterr()
    assert "credentialed-value" not in captured.out
    assert "credentialed-value" not in captured.err

    repository_source = repository_path.read_text(encoding="utf-8").upper()
    composition_source = composition_path.read_text(encoding="utf-8").upper()
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "ALTER ", "CREATE ", "DROP "):
        assert forbidden not in repository_source
    assert 'SELECT * FROM IDENTITY.A2_IDENTITY_INVENTORY_SNAPSHOT_V1()' in repository_source
    for forbidden_base_table in (
        'PUBLIC."USER"',
        "PUBLIC.IDENTITY_VERIFICATION_SUBMISSION",
        "PUBLIC.IDENTITY_VERIFICATION_DECISION",
        "IDENTITY.IDENTITY_SUBJECT_CLAIM_REGISTRY",
        "IDENTITY.USER_MEMBER_SELF_LINK",
        "PUBLIC.SERVICE_ENROLLMENT",
    ):
        assert forbidden_base_table not in repository_source
    assert "REPEATABLE READ READ ONLY" in composition_source
    assert "KG_A2_IDENTITY_INVENTORY_ROLE" in composition_source
    assert "A2_INVENTORY_BASE_TABLE_AUTHORITY_FORBIDDEN" in composition_source
