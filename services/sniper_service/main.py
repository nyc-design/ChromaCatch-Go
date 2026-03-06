"""FastAPI sniper service.

This service monitors Discord messages (self-client), queues coordinates that match
configured watch blocks, and dispatches queued coordinates to the location backend.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException

from shared.constants import setup_logging
from sniper_service.config import sniper_settings
from sniper_service.models import (
    DispatchResponse,
    EnqueueCoordinateRequest,
    QueueDispatchRequest,
    QueueStateResponse,
    SetWatchBlocksRequest,
    WatchBlock,
)
from sniper_service.monitor import DiscordMonitor
from sniper_service.service import SniperService

setup_logging()
logger = logging.getLogger(__name__)

service = SniperService(sniper_settings)
monitor = DiscordMonitor(sniper_settings.discord_token, service.handle_discord_message)

app = FastAPI(
    title="ChromaCatch Sniper Service",
    description=(
        "Discord coordinate intake and queue dispatcher for location spoofing workflows."
    ),
    version="0.1.0",
)


@app.on_event("startup")
async def startup() -> None:
    service.load_watch_blocks()
    await monitor.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await monitor.stop()


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "role": "sniper-service",
        "queue_size": service.queue_size,
        "active_client_id": service.active_client_id,
        "discord_monitor_enabled": monitor.enabled,
        "discord_monitor_connected": monitor.connected,
    }


@app.get("/watch-blocks")
async def get_watch_blocks() -> dict:
    return {"watch_blocks": [block.model_dump() for block in service.get_watch_blocks()]}


@app.put("/watch-blocks")
async def replace_watch_blocks(req: SetWatchBlocksRequest, client_id: str | None = None) -> dict:
    updated = service.replace_watch_blocks(req.watch_blocks, client_id=client_id)
    return {"watch_blocks": [block.model_dump() for block in updated]}


@app.post("/watch-blocks", response_model=WatchBlock)
async def add_watch_block(block: WatchBlock, client_id: str | None = None) -> WatchBlock:
    return service.add_watch_block(block, client_id=client_id)


@app.delete("/watch-blocks/{block_id}")
async def delete_watch_block(block_id: str) -> dict:
    deleted = service.delete_watch_block(block_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Watch block not found")
    return {"status": "deleted", "id": block_id}


@app.get("/queue", response_model=QueueStateResponse)
async def get_queue() -> QueueStateResponse:
    return service.queue_state()


@app.post("/queue/enqueue")
async def enqueue_manual(req: EnqueueCoordinateRequest) -> dict:
    item = service.enqueue_manual(req)
    if item is None:
        return {
            "status": "duplicate",
            "queue": service.queue_state(),
        }
    return {
        "status": "queued",
        "item": item,
        "queue": service.queue_state(),
    }


@app.post("/queue/clear", response_model=QueueStateResponse)
async def clear_queue() -> QueueStateResponse:
    return service.clear_queue()


@app.post("/queue/dispatch-next", response_model=DispatchResponse)
async def dispatch_next(req: QueueDispatchRequest) -> DispatchResponse:
    result = await service.dispatch_next(req)
    if not result.success and result.message == "Queue is empty":
        raise HTTPException(status_code=404, detail=result.message)
    if not result.success:
        raise HTTPException(status_code=502, detail=result.message)
    return result
