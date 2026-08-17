from pathlib import Path


def test_Application仅取单用户TOTP_AAD_envelope且无表权限():
    source = (Path(__file__).parents[1] / "app/migrations/versions/20260817_0021_phase1_slice2_therapist_qualification_service_ready.py").read_text(encoding="utf-8")
    assert "therapist_totp_for_login_v1(p_user_id BIGINT)" in source
    assert "SECURITY DEFINER" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "GRANT SELECT ON TABLE public.therapist_profile" not in source.split('application = os.environ["KG_DATABASE_USER"]')[1]
