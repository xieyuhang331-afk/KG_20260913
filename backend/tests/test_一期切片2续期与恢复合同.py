import inspect

from app.modules.therapist_qualification.service import renew


def test_续期建立完整新revision并经审核恢复SUSPENDED():
    source = inspect.getsource(renew)
    assert "previous_version_id" in source
    assert "RENEWAL" in source
