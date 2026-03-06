"""Core sniper service runtime and state management."""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

import httpx

from sniper_service.config import SniperSettings
from sniper_service.models import (
    CoordinateQueueItem,
    DispatchResponse,
    EnqueueCoordinateRequest,
    QueueDispatchRequest,
    QueueStateResponse,
    WatchBlock,
)
from sniper_service.parser import (
    extract_coordinate,
    flatten_discord_message_parts,
    parse_despawn_epoch,
)

logger = logging.getLogger(__name__)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two coordinates in km."""
    radius_km = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class SniperService:
    def __init__(self, settings: SniperSettings):
        self.settings = settings
        self._watch_blocks: list[WatchBlock] = []
        self._queue: list[CoordinateQueueItem] = []
        self._watch_blocks_path = Path(settings.watch_blocks_path)
        self._active_client_id: str | None = settings.location_client_id

    # --------- Watch blocks ---------

    def load_watch_blocks(self) -> None:
        if not self._watch_blocks_path.exists():
            self._watch_blocks = []
            return

        raw = json.loads(self._watch_blocks_path.read_text())
        self._watch_blocks = [WatchBlock.model_validate(item) for item in raw]

    def save_watch_blocks(self) -> None:
        self._watch_blocks_path.parent.mkdir(parents=True, exist_ok=True)
        data = [block.model_dump(mode="json") for block in self._watch_blocks]
        self._watch_blocks_path.write_text(json.dumps(data, indent=2))

    def get_watch_blocks(self) -> list[WatchBlock]:
        return list(self._watch_blocks)

    def set_active_client_id(self, client_id: str | None) -> None:
        normalized = (client_id or "").strip()
        if normalized:
            self._active_client_id = normalized

    @property
    def active_client_id(self) -> str | None:
        return self._active_client_id

    def replace_watch_blocks(
        self,
        watch_blocks: list[WatchBlock],
        client_id: str | None = None,
    ) -> list[WatchBlock]:
        self.set_active_client_id(client_id)
        self._watch_blocks = list(watch_blocks)
        self.save_watch_blocks()
        return self.get_watch_blocks()

    def add_watch_block(self, watch_block: WatchBlock, client_id: str | None = None) -> WatchBlock:
        self.set_active_client_id(client_id)
        self._watch_blocks.append(watch_block)
        self.save_watch_blocks()
        return watch_block

    def delete_watch_block(self, block_id: str) -> bool:
        before = len(self._watch_blocks)
        self._watch_blocks = [block for block in self._watch_blocks if block.id != block_id]
        changed = len(self._watch_blocks) != before
        if changed:
            self.save_watch_blocks()
        return changed

    # --------- Queue ---------

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    def queue_state(self) -> QueueStateResponse:
        self.prune_expired_queue()
        return QueueStateResponse(
            size=len(self._queue),
            max_size=self.settings.queue_max,
            items=list(self._queue),
        )

    def clear_queue(self) -> QueueStateResponse:
        self._queue = []
        return self.queue_state()

    def _dedupe_key(self, lat: float, lon: float) -> str:
        return f"{lat:.6f},{lon:.6f}"

    def prune_expired_queue(self, now_epoch: float | None = None) -> int:
        now = now_epoch or time.time()
        kept: list[CoordinateQueueItem] = []
        removed = 0
        for item in self._queue:
            if item.despawn_epoch is not None and item.despawn_epoch <= now:
                removed += 1
                continue
            kept.append(item)
        self._queue = kept
        return removed

    def enqueue_coordinate(
        self,
        latitude: float,
        longitude: float,
        source: str,
        despawn_epoch: float | None = None,
        matched_block_id: str | None = None,
        matched_server_id: str | None = None,
        matched_channel_id: str | None = None,
        matched_user_id: str | None = None,
        source_message_id: str | None = None,
    ) -> CoordinateQueueItem | None:
        self.prune_expired_queue()
        key = self._dedupe_key(latitude, longitude)
        for item in self._queue:
            if self._dedupe_key(item.latitude, item.longitude) == key:
                return None

        queued_item = CoordinateQueueItem(
            latitude=latitude,
            longitude=longitude,
            source=source,
            matched_block_id=matched_block_id,
            matched_server_id=matched_server_id,
            matched_channel_id=matched_channel_id,
            matched_user_id=matched_user_id,
            source_message_id=source_message_id,
            despawn_epoch=despawn_epoch,
        )
        self._queue.append(queued_item)

        if len(self._queue) > self.settings.queue_max:
            self._queue = self._queue[-self.settings.queue_max :]

        return queued_item

    def enqueue_manual(self, req: EnqueueCoordinateRequest) -> CoordinateQueueItem | None:
        return self.enqueue_coordinate(
            latitude=req.latitude,
            longitude=req.longitude,
            source=req.source,
        )

    async def dispatch_next(self, req: QueueDispatchRequest) -> DispatchResponse:
        self.prune_expired_queue()
        if not self._queue:
            return DispatchResponse(
                success=False,
                message="Queue is empty",
                queue=self.queue_state(),
            )

        # LIFO by design: newest coordinate is dispatched first.
        index = -1
        item = self._queue[index]

        payload = {
            "client_id": req.client_id or self._active_client_id or self.settings.location_client_id,
            "latitude": item.latitude,
            "longitude": item.longitude,
            "altitude": req.altitude if req.altitude is not None else self.settings.location_altitude,
            "speed_knots": (
                req.speed_knots
                if req.speed_knots is not None
                else self.settings.location_speed_knots
            ),
            "heading": req.heading if req.heading is not None else self.settings.location_heading,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.settings.location_post_url, json=payload)
            response.raise_for_status()
            body = response.json() if response.content else None
        except Exception as exc:
            logger.error("Failed dispatching queued coordinate: %s", exc)
            return DispatchResponse(
                success=False,
                message=str(exc),
                sent=item,
                queue=self.queue_state(),
            )

        self._queue.pop(index)
        return DispatchResponse(
            success=True,
            sent=item,
            location_response=body,
            queue=self.queue_state(),
        )

    # --------- Discord ingestion ---------

    async def handle_discord_message(self, message: object) -> None:
        """Parse/queue coords from a discord.py(-self) message object."""
        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)
        author = getattr(message, "author", None)

        server_id = str(getattr(guild, "id", "")) if guild else ""
        channel_id = str(getattr(channel, "id", "")) if channel else ""
        user_id = str(getattr(author, "id", "")) if author else ""

        if not server_id or not channel_id or not user_id:
            return

        matching_block: WatchBlock | None = None
        for block in self._watch_blocks:
            if not block.enabled:
                continue
            if block.server_id != server_id:
                continue
            if block.channel_id != channel_id:
                continue
            if user_id not in block.user_ids:
                continue
            matching_block = block
            break

        if matching_block is None:
            return

        content = str(getattr(message, "content", "") or "")
        embeds = []
        for embed in list(getattr(message, "embeds", []) or []):
            embeds.append(embed.to_dict() if hasattr(embed, "to_dict") else {})

        components = []
        for row in list(getattr(message, "components", []) or []):
            row_data = {"components": []}
            for comp in list(getattr(row, "children", []) or []):
                row_data["components"].append(
                    {
                        "label": getattr(comp, "label", None),
                        "custom_id": getattr(comp, "custom_id", None),
                        "url": getattr(comp, "url", None),
                    }
                )
            components.append(row_data)

        flattened = flatten_discord_message_parts(content, embeds, components)
        coords = extract_coordinate(flattened)
        if coords is None:
            return

        lat, lon = coords
        despawn_epoch = parse_despawn_epoch(
            flattened,
            reference_time=getattr(message, "created_at", None),
        )

        geofence = matching_block.geofence
        if geofence is not None:
            distance = haversine_km(lat, lon, geofence.latitude, geofence.longitude)
            if distance > geofence.radius_km:
                return

        queued = self.enqueue_coordinate(
            latitude=lat,
            longitude=lon,
            source="discord",
            despawn_epoch=despawn_epoch,
            matched_block_id=matching_block.id,
            matched_server_id=server_id,
            matched_channel_id=channel_id,
            matched_user_id=user_id,
            source_message_id=str(getattr(message, "id", "")) or None,
        )

        if queued is not None:
            logger.info(
                "Queued coordinate %.6f, %.6f from guild=%s channel=%s user=%s (queue=%d)",
                lat,
                lon,
                server_id,
                channel_id,
                user_id,
                len(self._queue),
            )
