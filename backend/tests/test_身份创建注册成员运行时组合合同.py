from importlib import import_module

from app.modules.member.application.create_registration_member import (
    CreateRegistrationMemberService,
)


class SessionFactorySpy:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("service construction must not create a session")


class UuidGeneratorSpy:
    def __init__(self):
        self.calls = 0

    def generate(self):
        self.calls += 1
        raise AssertionError("service construction must not generate a UUID")


def test_创建注册成员运行时组合尚未实现():
    module = import_module("app.composition.identity_persistence")
    factory = getattr(module, "create_registration_member_service", None)

    assert callable(factory)

    session_factory = SessionFactorySpy()
    uuid_generator = UuidGeneratorSpy()
    identity_persistence = module.IdentityPersistenceComposition(
        session_factory=session_factory,
        clock=lambda: None,
    )

    first = factory(
        identity_persistence=identity_persistence,
        uuid_generator=uuid_generator,
    )
    second = factory(
        identity_persistence=identity_persistence,
        uuid_generator=uuid_generator,
    )

    assert type(first) is CreateRegistrationMemberService
    assert type(second) is CreateRegistrationMemberService
    assert first is not second
    assert first._unit_of_work_factory.__self__ is identity_persistence
    assert first._unit_of_work_factory.__func__ is identity_persistence.unit_of_work.__func__
    assert first._uuid_generator is uuid_generator
    assert session_factory.calls == 0
    assert uuid_generator.calls == 0
