import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException


class HealthIndicatorServiceTests(unittest.IsolatedAsyncioTestCase):
    def _payload(self, *, source="APP", batch_id=None):
        from app.modules.user_health.schemas import HealthIndicatorBatchCreateRequest, HealthIndicatorCreateItem

        return HealthIndicatorBatchCreateRequest(
            indicators=[
                HealthIndicatorCreateItem(
                    indicator_type="blood_pressure",
                    value=Decimal("120.00"),
                    unit="mmHg",
                    source=source,
                    recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                    batch_id=batch_id,
                )
            ]
        )

    def _indicator(self, *, batch_id="batch-1", indicator_type="blood_pressure"):
        return SimpleNamespace(
            id=3001,
            user_id=1001,
            batch_id=batch_id,
            indicator_type=indicator_type,
            value=Decimal("120.00"),
            unit="mmHg",
            source="APP",
            recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc),
        )

    class FakeSession:
        def __init__(self):
            self.commit_called = False
            self.rollback_called = False

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    async def test_create_health_indicators_returns_404_when_user_missing(self):
        from app.modules.user_health.service import create_health_indicators

        session = self.FakeSession()
        create_mock = AsyncMock()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=None)),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock()),
            patch("app.modules.user_health.service.create_health_indicator_records", new=create_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await create_health_indicators(session, user_id=1001, payload=self._payload())

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "User not found")
        create_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_create_health_indicators_returns_409_when_user_inactive(self):
        from app.modules.user_health.service import create_health_indicators

        session = self.FakeSession()
        create_mock = AsyncMock()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="inactive"))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock()),
            patch("app.modules.user_health.service.create_health_indicator_records", new=create_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await create_health_indicators(session, user_id=1001, payload=self._payload())

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "User is not active")
        create_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_create_health_indicators_returns_409_when_profile_missing(self):
        from app.modules.user_health.service import create_health_indicators

        session = self.FakeSession()
        create_mock = AsyncMock()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="active"))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=None)),
            patch("app.modules.user_health.service.create_health_indicator_records", new=create_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await create_health_indicators(session, user_id=1001, payload=self._payload())

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Health profile is required")
        create_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_create_health_indicators_generates_batch_id_when_missing(self):
        from app.modules.user_health.service import create_health_indicators

        session = self.FakeSession()
        captured_records = []

        async def create_records(session_arg, *, records):
            self.assertIs(session_arg, session)
            captured_records.extend(records)
            return [self._indicator(batch_id=records[0]["batch_id"])]

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="active"))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=SimpleNamespace(id=2001))),
            patch("app.modules.user_health.service.create_health_indicator_records", new=AsyncMock(side_effect=create_records)),
        ):
            response = await create_health_indicators(session, user_id=1001, payload=self._payload(batch_id=None))

        self.assertEqual(len(captured_records), 1)
        self.assertEqual(len(captured_records[0]["batch_id"]), 36)
        self.assertEqual(captured_records[0]["user_id"], 1001)
        self.assertEqual(response[0].batch_id, captured_records[0]["batch_id"])
        self.assertTrue(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_create_health_indicators_returns_422_for_invalid_source(self):
        from app.modules.user_health.service import create_health_indicators

        session = self.FakeSession()
        payload = self._payload()
        payload.indicators[0].source = "MANUAL"
        create_mock = AsyncMock()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="active"))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=SimpleNamespace(id=2001))),
            patch("app.modules.user_health.service.create_health_indicator_records", new=create_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await create_health_indicators(session, user_id=1001, payload=payload)

        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(context.exception.detail, "Invalid health indicator source")
        create_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_create_health_indicators_successfully(self):
        from app.modules.user_health.service import create_health_indicators

        session = self.FakeSession()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="active"))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=SimpleNamespace(id=2001))),
            patch("app.modules.user_health.service.create_health_indicator_records", new=AsyncMock(return_value=[self._indicator(batch_id="existing-batch")])),
        ):
            response = await create_health_indicators(session, user_id=1001, payload=self._payload(batch_id="existing-batch"))

        self.assertEqual(len(response), 1)
        self.assertEqual(response[0].id, 3001)
        self.assertEqual(response[0].batch_id, "existing-batch")
        self.assertEqual(response[0].indicator_type, "blood_pressure")
        self.assertTrue(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_health_indicators_successfully(self):
        from app.modules.user_health.service import list_health_indicators

        session = self.FakeSession()
        start_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end_at = datetime(2026, 7, 29, tzinfo=timezone.utc)
        list_mock = AsyncMock(return_value=[self._indicator()])

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="active"))),
            patch("app.modules.user_health.service.list_health_indicators_by_user", new=list_mock),
        ):
            response = await list_health_indicators(
                session,
                user_id=1001,
                indicator_type="blood_pressure",
                start_at=start_at,
                end_at=end_at,
                limit=20,
            )

        list_mock.assert_awaited_once_with(
            session,
            user_id=1001,
            indicator_type="blood_pressure",
            start_at=start_at,
            end_at=end_at,
            limit=20,
        )
        self.assertEqual(response[0].id, 3001)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_health_indicators_returns_404_when_user_missing(self):
        from app.modules.user_health.service import list_health_indicators

        session = self.FakeSession()
        list_mock = AsyncMock()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=None)),
            patch("app.modules.user_health.service.list_health_indicators_by_user", new=list_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await list_health_indicators(session, user_id=1001)

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "User not found")
        list_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_health_indicators_returns_empty_result(self):
        from app.modules.user_health.service import list_health_indicators

        session = self.FakeSession()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="active"))),
            patch("app.modules.user_health.service.list_health_indicators_by_user", new=AsyncMock(return_value=[])),
        ):
            response = await list_health_indicators(session, user_id=1001)

        self.assertEqual(response, [])
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_health_indicators_response_does_not_expose_sensitive_fields(self):
        from app.modules.user_health.service import list_health_indicators

        session = self.FakeSession()
        indicator = self._indicator()
        indicator.password_hash = "secret"
        indicator.id_card = "110101199001011234"
        indicator.real_name = "Zhang San"

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="active"))),
            patch("app.modules.user_health.service.list_health_indicators_by_user", new=AsyncMock(return_value=[indicator])),
        ):
            response = await list_health_indicators(session, user_id=1001)

        data = response[0].model_dump()
        self.assertNotIn("password_hash", data)
        self.assertNotIn("id_card", data)
        self.assertNotIn("real_name", data)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_latest_health_indicators_successfully(self):
        from app.modules.user_health.service import get_latest_health_indicators

        session = self.FakeSession()
        latest_mock = AsyncMock(return_value=[self._indicator(indicator_type="blood_pressure")])

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="active"))),
            patch(
                "app.modules.user_health.service.list_latest_health_indicators_by_user",
                new=latest_mock,
            ),
        ):
            response = await get_latest_health_indicators(session, user_id=1001)

        latest_mock.assert_awaited_once_with(session, user_id=1001)
        self.assertEqual(response[0].indicator_type, "blood_pressure")
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_latest_health_indicators_returns_404_when_user_missing(self):
        from app.modules.user_health.service import get_latest_health_indicators

        session = self.FakeSession()
        latest_mock = AsyncMock()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=None)),
            patch("app.modules.user_health.service.list_latest_health_indicators_by_user", new=latest_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await get_latest_health_indicators(session, user_id=1001)

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "User not found")
        latest_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_latest_health_indicators_returns_empty_result(self):
        from app.modules.user_health.service import get_latest_health_indicators

        session = self.FakeSession()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="active"))),
            patch("app.modules.user_health.service.list_latest_health_indicators_by_user", new=AsyncMock(return_value=[])),
        ):
            response = await get_latest_health_indicators(session, user_id=1001)

        self.assertEqual(response, [])
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_latest_health_indicators_response_does_not_expose_sensitive_fields(self):
        from app.modules.user_health.service import get_latest_health_indicators

        session = self.FakeSession()
        indicator = self._indicator()
        indicator.password_hash = "secret"
        indicator.id_card = "110101199001011234"
        indicator.real_name = "Zhang San"

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001, status="active"))),
            patch("app.modules.user_health.service.list_latest_health_indicators_by_user", new=AsyncMock(return_value=[indicator])),
        ):
            response = await get_latest_health_indicators(session, user_id=1001)

        data = response[0].model_dump()
        self.assertNotIn("password_hash", data)
        self.assertNotIn("id_card", data)
        self.assertNotIn("real_name", data)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)
