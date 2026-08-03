from datetime import datetime, timedelta
from uuid import RFC_4122, UUID

from .mapper import MemberPersistenceState
from .models import MemberOrmModel


class MemberOrmIntegrationError(ValueError):
    """Raised when persistence state and ORM metadata cannot be converted."""


class MemberOrmStateMapper:
    @staticmethod
    def _is_uuid_v7(value: object) -> bool:
        return (
            type(value) is UUID
            and value.version == 7
            and value.variant == RFC_4122
        )

    @staticmethod
    def _is_utc_timestamp(value: object) -> bool:
        return (
            type(value) is datetime
            and value.tzinfo is not None
            and value.utcoffset() == timedelta(0)
        )

    def to_new_model(
        self,
        state: MemberPersistenceState,
        *,
        initial_version: int,
        created_at: datetime,
        updated_at: datetime,
    ) -> MemberOrmModel:
        if type(state) is not MemberPersistenceState:
            raise MemberOrmIntegrationError(
                "member persistence state is invalid"
            )
        if not self._is_uuid_v7(state.member_id):
            raise MemberOrmIntegrationError(
                "member persistence state is invalid"
            )
        if state.version is not None or type(initial_version) is not int:
            raise MemberOrmIntegrationError(
                "member persistence version is invalid"
            )
        if initial_version != 1:
            raise MemberOrmIntegrationError(
                "member persistence version is invalid"
            )
        if (
            not self._is_utc_timestamp(created_at)
            or not self._is_utc_timestamp(updated_at)
            or created_at != updated_at
        ):
            raise MemberOrmIntegrationError(
                "member audit timestamps are invalid"
            )

        return MemberOrmModel(
            member_id=state.member_id,
            member_no=state.member_no,
            creation_source=state.creation_source,
            status=state.status,
            version=initial_version,
            created_at=created_at,
            updated_at=updated_at,
        )

    def to_state(self, model: MemberOrmModel) -> MemberPersistenceState:
        if type(model) is not MemberOrmModel:
            raise MemberOrmIntegrationError("member orm model is invalid")
        if not self._is_uuid_v7(model.member_id):
            raise MemberOrmIntegrationError("member orm model is invalid")
        if type(model.version) is not int or model.version < 1:
            raise MemberOrmIntegrationError(
                "member persistence version is invalid"
            )

        return MemberPersistenceState(
            member_id=model.member_id,
            member_no=model.member_no,
            creation_source=model.creation_source,
            status=model.status,
            version=model.version,
        )
