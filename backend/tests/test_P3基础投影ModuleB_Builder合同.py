import asyncio
from types import SimpleNamespace


class _Generation:
    id = None
    projection_version = 1
    generation_no = 1
    status = "BUILDING"
    high_watermark = {"max_organization_id": 8}
    digest_key_id = "k1"
    input_digest = "a" * 64
    start_operation_id = "00000000-0000-0000-0000-000000000001"
    builder_id = "00000000-0000-0000-0000-000000000002"
    lease_epoch = 0
    lease_expires_at = None
    version = None


class _Checkpoint:
    generation_id = None
    last_source_id = None
    processed_count = 0
    projected_count = 0
    skipped_count = 0
    remaining_count = 2
    last_operation_id = None
    checkpoint_digest = "b" * 64
    version = None


class _Result:
    def scalar_one(self):
        return 41


class _Session:
    def __init__(self):
        self.events = []

    def add(self, value):
        raise AssertionError("session.add is outside the persistence contract")

    async def flush(self):
        raise AssertionError("session.flush is outside the persistence contract")

    async def execute(self, statement):
        returning = tuple(column.name for column in statement._returning)
        values = statement.compile().params
        self.events.append((statement.table.name, returning, values))
        return _Result()


def test_generation先flush并回填checkpoint外键():
    from app.modules.organization_projection.repository import OrganizationProjectionRepository

    session = _Session()
    generation = _Generation()
    checkpoint = _Checkpoint()
    repository = object.__new__(OrganizationProjectionRepository)
    repository.session = session

    asyncio.run(repository.add_generation(generation, checkpoint))

    assert generation.id == 41
    assert generation.version == 1
    assert checkpoint.generation_id == 41
    assert checkpoint.version == 1
    assert [event[0] for event in session.events] == [
        "organization_projection_generation",
        "organization_projection_checkpoint",
    ]
    assert session.events[0][1] == ("id",)
    assert session.events[1][1] == ()
    assert "id" not in session.events[0][2]
    assert session.events[1][2]["generation_id"] == 41


def test_source读取只使用冻结最小列投影():
    from pathlib import Path

    organization = Path("app/modules/organization_projection/repository.py").read_text(encoding="utf-8")
    health = Path("app/modules/health_projection/repository.py").read_text(encoding="utf-8")
    assert "select(self.source)" not in organization
    assert "select(CanonicalHealthFactOrmModel)" not in health
    for column in ("id", "parent_id", "org_name", "org_code", "org_type", "status", "sort_order", "version"):
        assert f"self.source.c.{column}" in organization
    for column in ("id", "subject_user_id", "indicator_code", "numeric_value", "unit", "measured_at", "received_at", "source_type"):
        assert f"self.source.c.{column}" in health


def test_organization_builder不使用包含await的同步生成式():
    from pathlib import Path

    source = Path("app/modules/organization_projection/service.py").read_text(encoding="utf-8")
    assert "tuple(build_organization_projection_row(chain=await" not in source


def test_session_lock_cancellation_cleanup_is_shielded():
    from pathlib import Path
    source = Path("app/modules/organization_projection/service.py").read_text(encoding="utf-8")
    lock = source.split("class ProjectionSessionLock", 1)[1].split("class OrganizationProjectionBuilder", 1)[0]
    assert "await self._finish_cleanup()" in lock
    assert "await asyncio.shield(task)" in lock


def test_health_selection_is_append_only_and_created_only_at_completion():
    from pathlib import Path
    repository = Path("app/modules/health_projection/repository.py").read_text(encoding="utf-8")
    service = Path("app/modules/health_projection/service.py").read_text(encoding="utf-8")
    assert "replace_selection" not in repository
    page = service.split("async def build_page", 1)[1].split("\n    async def complete", 1)[0]
    complete = service.split("async def complete", 1)[1].split("\n    async def takeover", 1)[0]
    assert "add_selections" not in page
    assert "add_selections" in complete


def test_completion_recomputes_every_digest_with_generation_stored_key():
    from pathlib import Path
    organization = Path("app/modules/organization_projection/repository.py").read_text(encoding="utf-8")
    health = Path("app/modules/health_projection/repository.py").read_text(encoding="utf-8")
    assert "build_organization_projection_row" in organization
    assert "row_digest" in organization.split("async def validate_completion", 1)[1]
    assert "build_health_projection_rows" in health.split("async def validate_completion", 1)[1]
    assert "selection_digest" in health.split("async def validate_completion", 1)[1]


def test_health_completion_locks_and_rereads_each_window():
    from pathlib import Path
    body = Path("app/modules/health_projection/service.py").read_text(encoding="utf-8").split("async def complete", 1)[1].split("\n    async def ", 1)[0]
    assert "list_projected_windows" in body
    assert "acquire_window_lock" in body
    assert "load_projected_window" in body
    assert body.index("acquire_window_lock") < body.index("load_projected_window")


def test_organization_completion_uses_fresh_full_snapshot_recalculation():
    from pathlib import Path
    body = Path("app/modules/organization_projection/repository.py").read_text(encoding="utf-8").split("async def validate_completion", 1)[1]
    assert "list_leaf_ids" in body
    assert "load_chain" in body
    assert "build_organization_projection_row" in body


def test_organization_completion_awaits_each_chain_in_source_order():
    from app.modules.organization_projection.domain import (
        OrganizationSourceNode,
        build_organization_projection_row,
    )
    from app.modules.organization_projection.repository import OrganizationProjectionRepository

    key = b"k" * 32
    chains = {
        4: (
            OrganizationSourceNode(4, 3, "K-1", "County 1", "county", "active", 0, 1),
            OrganizationSourceNode(3, 2, "C-1", "City", "city", "active", 0, 1),
            OrganizationSourceNode(2, 1, "P-1", "Province", "province", "active", 0, 1),
            OrganizationSourceNode(1, None, "HQ", "HQ", "headquarter", "active", 0, 1),
        ),
        8: (
            OrganizationSourceNode(8, 7, "K-2", "County 2", "county", "active", 1, 1),
            OrganizationSourceNode(7, 6, "C-2", "City 2", "city", "active", 0, 1),
            OrganizationSourceNode(6, 5, "P-2", "Province 2", "province", "active", 0, 1),
            OrganizationSourceNode(5, None, "HQ-2", "HQ 2", "headquarter", "active", 0, 1),
        ),
    }
    projected = tuple(build_organization_projection_row(chain=chains[source_id], digest_key=key) for source_id in (4, 8))
    persisted = tuple(SimpleNamespace(
        organization_id=row.organization_id, parent_id=row.parent_id,
        org_code=row.org_code, org_name=row.org_name, org_type=row.org_type,
        status=row.status, sort_order=row.sort_order, source_version=row.source_version,
        path_ids=list(row.path_ids), path_codes=list(row.path_codes),
        compatibility_mode=row.compatibility_mode, scope_eligible=row.scope_eligible,
        row_digest=row.row_digest, digest_key_id="k1",
    ) for row in projected)
    order = []

    class Result:
        def scalars(self): return iter(persisted)

    class Session:
        async def execute(self, _): return Result()

    repository = object.__new__(OrganizationProjectionRepository)
    repository.session = Session()

    async def list_leaf_ids(**_): return (4, 8)
    async def load_chain(source_id):
        order.append(source_id)
        return chains[source_id]

    repository.list_leaf_ids = list_leaf_ids
    repository.load_chain = load_chain
    checkpoint = SimpleNamespace(processed_count=2, projected_count=2, remaining_count=0)

    evidence = asyncio.run(repository.validate_completion(
        9, checkpoint, "k1", key, {"max_organization_id": 8}
    ))

    assert order == [4, 8]
    assert isinstance(evidence, str) and len(evidence) == 64
