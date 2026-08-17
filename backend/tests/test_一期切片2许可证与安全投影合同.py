from pathlib import Path


def test_许可证日期权威写入和无PII_readiness_source():
    root = Path(__file__).parents[1]
    migration = (root / "app/migrations/versions/20260817_0021_phase1_slice2_therapist_qualification_service_ready.py").read_text(encoding="utf-8")
    schemas = (root / "app/modules/institution_onboarding/schemas.py").read_text(encoding="utf-8")
    assert 'sa.Column("valid_from", sa.Date())' in migration
    assert "class LicenseBinding" in schemas and "valid_until: date" in schemas
    view = migration.split("CREATE VIEW public.institution_readiness_source_v1", 1)[1].split('"""', 1)[0]
    assert "ciphertext" not in view and "contact_phone" not in view
