from datetime import datetime, timezone

from app.core.uuid_generator import Uuid7Generator


def _clock():
    return datetime.now(timezone.utc)


def test_权威验证转换运行时组合尚未实现():
    from app.composition.p1_verified_transition import (
        create_manual_identity_review_verified_transition_service,
    )

    authority_port = object()
    session_factory = object()
    service = create_manual_identity_review_verified_transition_service(
        authority_port=authority_port,
        verification_session_factory=session_factory,
        uuid_generator=Uuid7Generator(),
    )

    assert service._authority_port is authority_port
    writer = service._transition_writer
    assert writer._uuid_generator is not None
    first_uow = writer._unit_of_work_factory()
    second_uow = writer._unit_of_work_factory()
    assert first_uow is not second_uow
    assert first_uow._session_factory is session_factory
    assert second_uow._session_factory is session_factory


def test_运行时组合不打开Session且每次创建新鲜UoW():
    from app.composition.p1_verified_transition import (
        create_manual_identity_review_verified_transition_service,
    )

    opened = []

    def session_factory():
        opened.append(True)
        raise AssertionError("composition must not open a session")

    service = create_manual_identity_review_verified_transition_service(
        authority_port=object(),
        verification_session_factory=session_factory,
        uuid_generator=Uuid7Generator(),
    )
    first = service._transition_writer._unit_of_work_factory()
    second = service._transition_writer._unit_of_work_factory()
    assert opened == []
    assert first is not second
