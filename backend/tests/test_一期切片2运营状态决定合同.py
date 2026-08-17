from app.modules.therapist_qualification.models import TherapistStatusDecisionModel


def test_暂停恢复退出使用独立append_only_decision():
    assert TherapistStatusDecisionModel.__tablename__ == "therapist_status_decision"
    assert "expected_profile_version" in TherapistStatusDecisionModel.__table__.columns
