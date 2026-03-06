"""Pydantic models for sniper API."""

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class Geofence(BaseModel):
    latitude: float
    longitude: float
    radius_km: float = Field(gt=0)


class WatchBlock(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    server_id: str
    channel_id: str
    user_ids: list[str] = Field(default_factory=list, min_length=1)
    geofence: Geofence | None = None
    enabled: bool = True

    @field_validator("server_id", "channel_id")
    @classmethod
    def normalize_ids(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("id cannot be empty")
        return value

    @field_validator("user_ids")
    @classmethod
    def normalize_user_ids(cls, value: list[str]) -> list[str]:
        normalized = []
        for user_id in value:
            user_id = str(user_id).strip()
            if user_id:
                normalized.append(user_id)
        if not normalized:
            raise ValueError("user_ids cannot be empty")
        return normalized


class SetWatchBlocksRequest(BaseModel):
    watch_blocks: list[WatchBlock]


class EnqueueCoordinateRequest(BaseModel):
    latitude: float
    longitude: float
    source: str = "manual"


class QueueDispatchRequest(BaseModel):
    client_id: str | None = None
    altitude: float | None = None
    speed_knots: float | None = None
    heading: float | None = None


class CoordinateQueueItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    latitude: float
    longitude: float
    source: str = "discord"
    matched_block_id: str | None = None
    matched_server_id: str | None = None
    matched_channel_id: str | None = None
    matched_user_id: str | None = None
    queued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_message_id: str | None = None
    despawn_epoch: float | None = None


class QueueStateResponse(BaseModel):
    size: int
    max_size: int
    items: list[CoordinateQueueItem]


class DispatchResponse(BaseModel):
    success: bool
    sent: CoordinateQueueItem | None = None
    location_response: dict | list | str | None = None
    queue: QueueStateResponse
    message: str | None = None
