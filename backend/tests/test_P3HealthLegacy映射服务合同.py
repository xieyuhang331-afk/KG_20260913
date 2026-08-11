import base64
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest


def test_Health_fact_mapping_audit由唯一外层commit完成():
    from app.modules.health_fact.domain import HealthFactDigestKeyring
    from app.modules.health_fact_mapping.domain import HealthIndicatorSourceSnapshot
    from app.modules.health_fact_mapping.service import HealthLegacyMappingService

    source=HealthIndicatorSourceSnapshot(1,2,"weight",Decimal("65.20"),"kg","APP",datetime.now(timezone.utc),datetime.now(timezone.utc),None)
    class Repo:
        existing=None; added=0; audits=0
        async def acquire_source_lock(self,*args): pass
        async def get_source(self,*args): return source
        async def find_existing(self,*args): return self.existing
        async def add(self,m): self.added+=1; self.existing=m; return m
        async def add_audit(self,*args): self.audits+=1
    class Uow:
        def __init__(self): self.mapping_repository=Repo(); self.fact_repository=object(); self.commits=0
        async def __aenter__(self): return self
        async def __aexit__(self,*args): return False
        async def commit(self): self.commits+=1
    class Writer:
        calls=0
        async def append_in_uow(self,draft,*,repository):
            self.calls+=1
            fact=type("Fact",(),{"id":99})()
            return type("Result",(),{"fact":fact,"outcome":"CREATED"})()
    uow=Uow(); writer=Writer()
    keyring=HealthFactDigestKeyring.from_base64(current_key_id="k1",encoded_keys={"k1":base64.b64encode(b"b"*32).decode()})
    service=HealthLegacyMappingService(writer=writer,uow_factory=lambda:uow,readonly_uow_factory=lambda:uow,keyring=keyring)
    result=asyncio.run(service.map_one(legacy_indicator_id=1,legacy_recorded_at=source.recorded_at,batch_id="b"))
    assert (result.outcome,writer.calls,uow.mapping_repository.added,uow.mapping_repository.audits,uow.commits)==("CREATED",1,1,1,1)


def test_Health_mapping提交结果未知fresh确认fact_mapping_audit且仍安全失败():
    from app.modules.health_fact.domain import HealthFactDigestKeyring
    from app.modules.health_fact_mapping.domain import HealthIndicatorSourceSnapshot, HealthLegacyMappingUnavailable
    from app.modules.health_fact_mapping.service import HealthLegacyMappingService

    source=HealthIndicatorSourceSnapshot(1,2,"weight",Decimal("65.20"),"kg","APP",datetime.now(timezone.utc),datetime.now(timezone.utc),None)
    class Repo:
        stored=None
        async def acquire_source_lock(self,*args): pass
        async def get_source(self,*args): return source
        async def find_existing(self,*args): return None
        async def add(self,m): self.stored=m; return m
        async def add_audit(self,*args): pass
    repo=Repo()
    class WriteUow:
        mapping_repository=repo; fact_repository=object()
        async def __aenter__(self): return self
        async def __aexit__(self,*args): return False
        async def commit(self): raise HealthLegacyMappingUnavailable("Health mapping commit outcome is unknown")
    class ReadRepo:
        async def find_existing(self,*args): return repo.stored
        async def has_audit(self,*args): return True
    class FactRepo:
        async def get_by_id(self, fact_id): return expected_fact
        async def has_audit(self, *, fact_id, action): return True
    class ReadUow:
        mapping_repository=ReadRepo()
        fact_repository=FactRepo()
        async def __aenter__(self): return self
        async def __aexit__(self,*args): return False
    expected_fact = type("Fact",(),{"id":99})()
    class Writer:
        async def append_in_uow(self,draft,*,repository):
            return type("Result",(),{"fact":expected_fact,"outcome":"CREATED"})()
    keyring=HealthFactDigestKeyring.from_base64(current_key_id="k1",encoded_keys={"k1":base64.b64encode(b"b"*32).decode()})
    service=HealthLegacyMappingService(writer=Writer(),uow_factory=WriteUow,readonly_uow_factory=ReadUow,keyring=keyring)
    with pytest.raises(HealthLegacyMappingUnavailable,match="commit outcome is unknown") as captured:
        asyncio.run(service.map_one(legacy_indicator_id=1,legacy_recorded_at=source.recorded_at,batch_id="b"))
    assert captured.value.__cause__ is None and captured.value.__context__ is None


def test_Health_mapping结果未知必须确认fact与两类审计():
    from app.modules.health_fact_mapping.domain import HealthIndicatorLegacyMapping, HealthLegacyMappingUnavailable
    from app.modules.health_fact_mapping.service import HealthLegacyMappingService
    mapping = HealthIndicatorLegacyMapping(1, datetime.now(timezone.utc), 99, 1, "b", "a"*64, "k1", "MAPPED", "MAPPED_EXACT", 8)
    fact = type("Fact", (), {"id": 99})()
    class MappingRepo:
        async def find_existing(self, *args): return mapping
        async def has_audit(self, *args): return True
    class FactRepo:
        async def get_by_id(self, fact_id): return fact
        async def has_audit(self, *, fact_id, action): return False
    class ReadUow:
        mapping_repository=MappingRepo(); fact_repository=FactRepo()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    service=HealthLegacyMappingService(writer=object(),uow_factory=ReadUow,readonly_uow_factory=ReadUow,keyring=object())
    with pytest.raises(HealthLegacyMappingUnavailable):
        asyncio.run(service._confirm_unknown(mapping, fact))
