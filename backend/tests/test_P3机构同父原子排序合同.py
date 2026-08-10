import pytest


def test_P3机构同父原子排序尚未实现():
    try:
        from app.modules.organization.service import calculate_reorder_versions
    except (ImportError, ModuleNotFoundError):
        pytest.fail("P3 Organization atomic sibling ordering is not implemented")

    rows = [
        {"id": 11, "sort_order": 0, "version": 3},
        {"id": 12, "sort_order": 1, "version": 4},
    ]
    result = calculate_reorder_versions(rows, {11: 1, 12: 0})
    assert result == [
        {"id": 11, "sort_order": 1, "version": 4},
        {"id": 12, "sort_order": 0, "version": 5},
    ]


def test_未变化child_version保持不变():
    from app.modules.organization.service import calculate_reorder_versions

    assert calculate_reorder_versions(
        [{"id": 11, "sort_order": 0, "version": 3}],
        {11: 0},
    ) == [{"id": 11, "sort_order": 0, "version": 3}]


class _OutcomeRepo:
    def __init__(self, organizations, children, audits):
        self.organizations = organizations
        self.children = children
        self.audits = audits

    async def get_organization(self, organization_id, **_kwargs):
        return self.organizations.get(organization_id)

    async def get_organization_by_code(self, org_code):
        return next(
            (row for row in self.organizations.values() if getattr(row, "org_code", None) == org_code),
            None,
        )

    async def list_children(self, parent_id):
        return self.children.get(parent_id, [])

    async def get_audit(self, object_id, action):
        return self.audits.get((object_id, action))


def test_all_mutation_outcome_confirmations_require_full_state_and_audit():
    import asyncio
    from types import SimpleNamespace

    from app.modules.organization.service import (
        _confirm_create_outcome,
        _confirm_patch_outcome,
        _confirm_reorder_outcome,
        _confirm_status_outcome,
    )

    parent = SimpleNamespace(id=1, version=8)
    child = SimpleNamespace(
        id=2, parent_id=1, org_code="TEST_CHILD", org_name="Test Child",
        org_type="province", status="active", sort_order=0, version=1,
        admin_id=None, created_at="created", updated_at="updated",
    )
    repo = _OutcomeRepo(
        {1: parent, 2: child},
        {1: [child]},
        {
            (2, "organization_created"): SimpleNamespace(
                operator_id=9,
                payload={"org_code": "TEST_CHILD", "parent_id": 1, "version": 1},
            ),
            (2, "organization_updated"): SimpleNamespace(
                operator_id=9, payload={"version": 1, "fields": ["org_name"]}
            ),
            (1, "organization_children_reordered"): SimpleNamespace(
                operator_id=9,
                payload={"parent_version": 8, "children": [{"id": 2, "sort_order": 0, "version": 1}]},
            ),
            (2, "organization_inactive"): SimpleNamespace(
                operator_id=9,
                payload={"status": "inactive", "version": 2, "reason_code": "PLATFORM_GOVERNANCE"},
            ),
        },
    )
    response = {
        "id": 2, "parent_id": 1, "org_code": "TEST_CHILD", "org_name": "Test Child",
        "org_type": "province", "status": "active", "compatibility_mode": "canonical",
        "sort_order": 0, "version": 1, "admin_user_id": None,
        "created_at": "created", "updated_at": "updated",
    }

    async def verify():
        assert await _confirm_create_outcome(
        repo, response=response, parent_id=1, parent_version=8, operator_id=9
        )
        assert await _confirm_patch_outcome(
            repo, response=response, fields=["org_name"], operator_id=9
        )
        assert await _confirm_reorder_outcome(
            repo, parent_id=1, parent_version=8,
            items=[{"organization_id": 2, "sort_order": 0, "version": 1}], operator_id=9,
        )

        child.status = "inactive"
        child.version = 2
        assert await _confirm_status_outcome(
            repo, organization_id=2, status="inactive", version=2,
            reason_code="PLATFORM_GOVERNANCE", operator_id=9,
        )

        repo.audits.clear()
        assert not await _confirm_create_outcome(
            repo, response=response, parent_id=1, parent_version=8, operator_id=9
        )

    asyncio.run(verify())


def test_commit_outcome_unknown_uses_a_fresh_confirmation_session(monkeypatch):
    import asyncio

    from app.modules.organization import service
    from app.modules.organization.domain import OrganizationError

    calls = []

    class Uow:
        async def commit(self):
            raise RuntimeError("fixed test commit failure")

        async def rollback(self):
            calls.append("rollback")

    class SessionContext:
        async def __aenter__(self):
            calls.append("fresh-session-enter")
            return object()

        async def __aexit__(self, *_args):
            calls.append("fresh-session-exit")

    class Factory:
        def __call__(self):
            return SessionContext()

    fresh_repo = object()
    monkeypatch.setattr(service, "OrganizationRepository", lambda _session: fresh_repo)

    async def confirm(repo):
        assert repo is fresh_repo
        calls.append("confirm")

    async def verify():
        with pytest.raises(OrganizationError) as exc:
            await service._commit(Uow(), lambda: Factory(), confirm)
        assert exc.value.code == "ORGANIZATION_COMMIT_OUTCOME_UNKNOWN"

    asyncio.run(verify())
    assert calls == ["rollback", "fresh-session-enter", "confirm", "fresh-session-exit"]
