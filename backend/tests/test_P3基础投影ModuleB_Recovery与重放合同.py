import asyncio
from datetime import UTC, datetime, timedelta

import pytest


class FakeLeaseRepository:
    def __init__(self): self.calls=[]
    async def get_generation(self, generation_id): return type("G", (), {"version": 7})()
    async def heartbeat(self, **kwargs): self.calls.append(kwargs); return True
    async def audit_payload(self, operation_id): return None
    async def add_audit(self, **kwargs): self.calls.append(kwargs)


def test_heartbeat_uses_fresh_uow_and_closes_it():
    from app.modules.organization_projection.service import ProjectionHeartbeat
    events=[]
    class Uow:
        repository=FakeLeaseRepository()
        async def __aenter__(self): events.append("enter"); return self
        async def __aexit__(self,*args): events.append("exit")
        async def commit(self): events.append("commit")
    asyncio.run(ProjectionHeartbeat(lambda: Uow()).beat(
        generation_id=1, builder_id="b", lease_epoch=2,
        operation_id="00000000-0000-0000-0000-000000000001",
    ))
    assert events == ["enter","commit","exit"]
    assert Uow.repository.calls[0]["operation_id"] == "00000000-0000-0000-0000-000000000001"
    assert Uow.repository.calls[1]["payload"]["preimage"] == {
        "operation": "heartbeat", "generation_id": 1, "builder_id": "b", "lease_epoch": 2,
    }
    assert "lease_expires_at" in Uow.repository.calls[1]["payload"]["postimage"]


def test_heartbeat_operation_id_is_required_and_not_random():
    from pathlib import Path
    source = Path("app/modules/organization_projection/service.py").read_text(encoding="utf-8")
    heartbeat = source.split("class ProjectionHeartbeat", 1)[1].split("class ProjectionUnitOfWork", 1)[0]
    assert "operation_id" in heartbeat.split("async def beat", 1)[1].split(":", 1)[0]
    assert "uuid4" not in heartbeat


def test_all_builder_mutations_use_the_session_lock():
    from pathlib import Path
    for path in (
        Path("app/modules/organization_projection/service.py"),
        Path("app/modules/health_projection/service.py"),
    ):
        source = path.read_text(encoding="utf-8")
        builder = source.split("class OrganizationProjectionBuilder", 1)[-1]
        if "class HealthProjectionBuilder" in source:
            builder = source.split("class HealthProjectionBuilder", 1)[1]
        for method in ("start", "build_page", "complete", "takeover"):
            body = builder.split(f"async def {method}", 1)[1]
            body = body.split("\n    async def ", 1)[0]
            assert "self._session_lock(" in body


def test_takeover_validates_checkpoint_before_mutation():
    from pathlib import Path
    for path in (
        Path("app/modules/organization_projection/service.py"),
        Path("app/modules/health_projection/service.py"),
    ):
        source = path.read_text(encoding="utf-8")
        body = source.split("async def takeover", 1)[1].split("\n    async def ", 1)[0]
        assert "get_checkpoint" in body
        assert "_validate_checkpoint" in body
        assert body.index("_validate_checkpoint") < body.index("lease_epoch += 1")


def test_build_page_checks_replay_before_fresh_heartbeat():
    from pathlib import Path
    for path in (
        Path("app/modules/organization_projection/service.py"),
        Path("app/modules/health_projection/service.py"),
    ):
        body = path.read_text(encoding="utf-8").split("async def build_page", 1)[1].split("\n    async def complete", 1)[0]
        assert "heartbeat_operation_id" in body.splitlines()[0]
        assert "await self.heartbeat.beat(" in body
        assert body.index("_replay_before_heartbeat") < body.index("await self.heartbeat.beat(")


def test_lock_cancellation_invalidates_and_propagates():
    from app.modules.organization_projection.service import ProjectionSessionLock
    events = []
    class Result:
        def scalar_one(self): return True
    class Connection:
        async def execute(self, *_): raise asyncio.CancelledError()
        async def invalidate(self): events.append("invalidate")
        async def close(self): events.append("close")
    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            async with ProjectionSessionLock(lambda: asyncio.sleep(0, result=Connection()), 1):
                pass
    asyncio.run(scenario())
    assert events == ["invalidate"]


def test_heartbeat_failure_prevents_business_uow():
    from app.modules.organization_projection.service import OrganizationProjectionBuilder, ProjectionDigestKeyring, ProjectionLeaseConflict
    events = []
    class ConfirmationRepository:
        async def get_generation(self, _): return type("G", (), {"builder_id": "b", "lease_epoch": 1})()
        async def audit_payload(self, _): return None
    class ConfirmationUow:
        repository = ConfirmationRepository()
        async def __aenter__(self): events.append("confirmation"); return self
        async def __aexit__(self, *_): pass
    class Heartbeat:
        async def beat(self, **_): events.append("heartbeat"); raise ProjectionLeaseConflict("x")
    builder = OrganizationProjectionBuilder(lambda: (_ for _ in ()).throw(AssertionError("business uow opened")), lambda: ConfirmationUow(), ProjectionDigestKeyring(current_key_id="k", keys={"k": b"k"*32}), lambda: None)
    builder.heartbeat = Heartbeat()
    with pytest.raises(ProjectionLeaseConflict):
        asyncio.run(builder.build_page(generation_id=1, builder_id="b", lease_epoch=1, expected_checkpoint_digest="d", operation_id="o", heartbeat_operation_id="h"))
    assert events == ["confirmation", "heartbeat"]


def test_unknown_stored_key_fails_closed():
    from app.modules.organization_projection.service import ProjectionDigestKeyring, ProjectionDigestKeyUnavailable
    ring=ProjectionDigestKeyring(current_key_id="k2", keys={"k2": b"x"*32})
    with pytest.raises(ProjectionDigestKeyUnavailable): ring.stored("k1")


def test_cancelled_error_is_not_mapped():
    from app.modules.organization_projection.repository import _safe
    async def cancelled(): raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError): asyncio.run(_safe(cancelled()))


def test_all_mutations_freeze_operation_preimage_and_replay_contract():
    from pathlib import Path
    for path in (
        Path("app/modules/organization_projection/service.py"),
        Path("app/modules/health_projection/service.py"),
    ):
        source = path.read_text(encoding="utf-8")
        for method in ("start", "build_page", "complete", "takeover", "fail", "supersede"):
            body = source.split(f"async def {method}", 1)[1].split("\n    async def ", 1)[0]
            assert "operation_id" in body.splitlines()[0]
            assert "_replay_operation" in body
            assert "preimage" in body


def test_lease_owned_mutations_require_epoch_and_live_lease():
    from pathlib import Path
    for path in (
        Path("app/modules/organization_projection/service.py"),
        Path("app/modules/health_projection/service.py"),
    ):
        source = path.read_text(encoding="utf-8")
        for method in ("build_page", "complete", "fail"):
            body = source.split(f"async def {method}", 1)[1].split("\n    async def ", 1)[0]
            assert "lease_epoch" in body.splitlines()[0]
            assert "_require_live_lease" in body


def test_confirmation_can_distinguish_rollback_on_existing_generation():
    from pathlib import Path
    for path in (
        Path("app/modules/organization_projection/service.py"),
        Path("app/modules/health_projection/service.py"),
    ):
        body = path.read_text(encoding="utf-8").split("async def confirm_operation", 1)[1]
        assert "expected_preimage" in body.splitlines()[0]
        assert "operation_preimage" in body


def test_start_confirmation_does_not_require_known_generation_id():
    from pathlib import Path
    for path in (Path("app/modules/organization_projection/service.py"), Path("app/modules/health_projection/service.py")):
        body = path.read_text(encoding="utf-8").split("async def confirm_operation", 1)[1]
        assert "generation_id: int | None" in body or "generation_id=None" in body
        assert "audit_record" in body


def test_supersede_requires_newer_complete_generation():
    from pathlib import Path
    for path in (Path("app/modules/organization_projection/service.py"), Path("app/modules/health_projection/service.py")):
        body = path.read_text(encoding="utf-8").split("async def supersede", 1)[1].split("\n    async def ", 1)[0]
        assert "has_newer_complete_generation" in body
        assert '"generation_version"' in body


def test_lock_cancellation_waits_for_cleanup_completion():
    from pathlib import Path
    source = Path("app/modules/organization_projection/service.py").read_text(encoding="utf-8")
    lock = source.split("class ProjectionSessionLock", 1)[1].split("class OrganizationProjectionBuilder", 1)[0]
    assert "_finish_cleanup" in lock
    assert "await task" in lock
