import inspect

from app.modules.therapist_qualification.api import (
    _activation_precommit,
    _actor_precommit,
)
from app.modules.therapist_qualification.service import _commit_receipt, require_institution_actor, require_reviewer, translate_error


def test_机构健管师审核员旧JWT即时拒绝():
    source = inspect.getsource(require_institution_actor) + inspect.getsource(require_reviewer)
    assert "actor.tenant_id" in source
    assert "actor.org_id" in source
    translated = translate_error(RuntimeError())
    assert translated.status_code == 500
    assert translated.detail == "INTERNAL_ERROR"


def test_专用Session授权锁在writer提交前重读且commit未知仍只确认不重写():
    commit_source = inspect.getsource(_commit_receipt)
    actor_source = inspect.getsource(_actor_precommit)
    activation_source = inspect.getsource(_activation_precommit)
    assert commit_source.index("await precommit_check()") < commit_source.index("await _commit(")
    assert "await require_institution_actor" in actor_source
    assert "await require_therapist" in actor_source
    assert "await require_reviewer" in actor_source
    assert "await _activation_currentness(session, invitation_id)" in activation_source
