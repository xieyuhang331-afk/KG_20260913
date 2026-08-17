from __future__ import annotations

from datetime import datetime
from typing import Protocol


class TherapistRepositoryPort(Protocol):
    async def invitation_for_update(self, invitation_id: str): ...
    async def profile_for_update(self, therapist_id: str): ...
    async def current_profile_for_user(self, user_id: int): ...


class PrivateFileQualificationPort(Protocol):
    async def lock_clean_files(self, *, file_ids: tuple[str, ...], owner_user_id: int): ...


class WorkflowPublisherPort(Protocol):
    async def publish(self, *, event_id: str) -> None: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...
