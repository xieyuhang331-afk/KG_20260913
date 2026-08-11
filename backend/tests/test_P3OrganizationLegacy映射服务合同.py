import asyncio
import base64
import json

import pytest


def _keyring():
    from app.modules.organization_mapping.domain import OrganizationMappingDigestKeyring
    key = base64.b64encode(b"a" * 32).decode()
    return OrganizationMappingDigestKeyring.from_json(
        current_key_id="k1", keyring_json=json.dumps({"k1": key})
    )


def test_机构mapping单commit稳定重放与漂移冲突():
    from app.modules.organization_mapping.domain import OrganizationMappingConflict, OrganizationSourceSnapshot
    from app.modules.organization_mapping.service import OrganizationLegacyMappingService

    class Repo:
        existing = None
        added = 0
        audits = 0
        async def acquire_source_lock(self, *args): pass
        async def get_source(self, _):
            return OrganizationSourceSnapshot(1, 2, {"id":2,"parent_id":1,"org_type":"city","status":"active","version":1})
        async def find_existing(self, *_): return self.existing
        async def add(self, value): self.added += 1; self.existing = value; return value
        async def add_audit(self, *_): self.audits += 1

    class Uow:
        def __init__(self, repo): self.repository=repo; self.commits=0
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def commit(self): self.commits += 1

    repo=Repo(); uow=Uow(repo)
    service=OrganizationLegacyMappingService(uow_factory=lambda:uow, readonly_uow_factory=lambda:uow, keyring=_keyring())
    first=asyncio.run(service.map_one(legacy_tenant_id=1,batch_id="b"))
    replay=asyncio.run(service.map_one(legacy_tenant_id=1,batch_id="b"))
    assert (first.outcome,replay.outcome,repo.added,repo.audits,uow.commits)==("CREATED","REPLAYED",1,1,1)
    repo.existing = type(repo.existing)(**{**{name:getattr(repo.existing,name) for name in repo.existing.__dataclass_fields__},"source_fingerprint":"0"*64})
    with pytest.raises(OrganizationMappingConflict):
        asyncio.run(service.map_one(legacy_tenant_id=1,batch_id="b"))


def test_Cancellation原样传播():
    from app.modules.organization_mapping.service import OrganizationLegacyMappingService
    class Uow:
        async def __aenter__(self): raise asyncio.CancelledError()
        async def __aexit__(self,*args): return False
    service=OrganizationLegacyMappingService(uow_factory=Uow,readonly_uow_factory=Uow,keyring=_keyring())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.map_one(legacy_tenant_id=1,batch_id="b"))


def test_机构mapping提交结果未知使用新鲜只读UoW确认且仍安全失败():
    from app.modules.organization_mapping.domain import OrganizationMappingUnavailable, OrganizationSourceSnapshot
    from app.modules.organization_mapping.service import OrganizationLegacyMappingService

    class WriteRepo:
        stored = None
        async def acquire_source_lock(self, *args): pass
        async def get_source(self, _):
            return OrganizationSourceSnapshot(1, 2, {"id":2,"parent_id":1,"org_type":"city","status":"active","version":1})
        async def find_existing(self, *_): return None
        async def add(self, value): self.stored = value; return value
        async def add_audit(self, *_): pass
    repo = WriteRepo()

    class WriteUow:
        repository = repo
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def commit(self): raise OrganizationMappingUnavailable("Organization mapping commit outcome is unknown")
    class ReadRepo:
        async def find_existing(self, *_): return repo.stored
        async def has_audit(self, *_): return True
    class ReadUow:
        repository = ReadRepo()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False

    service=OrganizationLegacyMappingService(uow_factory=WriteUow,readonly_uow_factory=ReadUow,keyring=_keyring())
    with pytest.raises(OrganizationMappingUnavailable, match="commit outcome is unknown") as captured:
        asyncio.run(service.map_one(legacy_tenant_id=1,batch_id="b"))
    assert captured.value.__cause__ is None and captured.value.__context__ is None


def test_机构mapping结果未知不得忽略缺失审计():
    from app.modules.organization_mapping.domain import OrganizationLegacyMapping, OrganizationMappingUnavailable
    from app.modules.organization_mapping.service import OrganizationLegacyMappingService

    expected = OrganizationLegacyMapping(1, 2, 2, 1, "b", "a"*64, "k1", "MAPPED", "MAPPED_EXACT", 9)
    class Repo:
        async def find_existing(self, *_): return expected
        async def has_audit(self, *_): return False
    class Uow:
        repository = Repo()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    service = OrganizationLegacyMappingService(uow_factory=Uow, readonly_uow_factory=Uow, keyring=_keyring())
    with pytest.raises(OrganizationMappingUnavailable):
        asyncio.run(service._confirm_unknown(expected))


@pytest.mark.parametrize(
    "changed_ancestor",
    [
        {"id": 1, "parent_id": None, "org_type": "headquarter", "status": "archived", "version": 2},
        {"id": 1, "parent_id": None, "org_type": "platform", "status": "active", "version": 2},
        {"id": 1, "parent_id": 99, "org_type": "headquarter", "status": "active", "version": 2},
        None,
    ],
)
def test_existing_mapping祖先漂移不得错误REPLAYED(changed_ancestor):
    from app.modules.organization_mapping.domain import (
        OrganizationMappingConflict,
        OrganizationSourceSnapshot,
        build_organization_mapping,
    )
    from app.modules.organization_mapping.service import OrganizationLegacyMappingService

    original = OrganizationSourceSnapshot(
        1, 2,
        {"id": 2, "parent_id": 1, "org_type": "province", "status": "active", "version": 1},
        ancestors=({"id": 1, "parent_id": None, "org_type": "headquarter", "status": "active", "version": 1},),
    )
    existing = build_organization_mapping(
        source=original, mapping_version=1, batch_id="b", keyring=_keyring()
    )
    changed = OrganizationSourceSnapshot(1, 2, original.target, ancestors=(changed_ancestor,))

    class Repo:
        async def acquire_source_lock(self, *args): pass
        async def get_source(self, _): return changed
        async def find_existing(self, *_): return existing
    class Uow:
        repository = Repo()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False

    service = OrganizationLegacyMappingService(
        uow_factory=Uow, readonly_uow_factory=Uow, keyring=_keyring()
    )
    with pytest.raises(OrganizationMappingConflict):
        asyncio.run(service.map_one(legacy_tenant_id=1, batch_id="b"))
