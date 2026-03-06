# Sniper Service (FastAPI)

Discord coordinate monitor + queue dispatcher for ChromaCatch.

## Purpose
- Watch multiple `(server_id, channel_id, user_ids[])` blocks
- Optionally enforce geofence per block
- Queue coordinates from Discord messages
- Queue uses LIFO dispatch semantics (newest coordinate sent first)
- Queue prunes expired entries using parsed despawn timers from Discord message text
- Dispatch next queued coordinate to location backend via `POST /location`

## Environment (`CC_SNIPER_`)
- `CC_SNIPER_DISCORD_TOKEN` - Discord user token for self-client monitoring
- `CC_SNIPER_API_HOST` - API bind host (default `0.0.0.0`)
- `CC_SNIPER_API_PORT` - API port (default `8010`)
- `CC_SNIPER_LOCATION_POST_URL` - location backend endpoint (default `http://location-backend:8001/location`)
- `CC_SNIPER_LOCATION_CLIENT_ID` - default `client_id` for dispatch requests
- `CC_SNIPER_LOCATION_ALTITUDE`, `CC_SNIPER_LOCATION_SPEED_KNOTS`, `CC_SNIPER_LOCATION_HEADING`
- `CC_SNIPER_QUEUE_MAX` - max queue size
- `CC_SNIPER_WATCH_BLOCKS_PATH` - persisted watch blocks JSON path

## API
- `GET /health`
- `GET /watch-blocks`
- `PUT /watch-blocks` (replace all; optional `client_id` query param to set active dispatch client)
- `POST /watch-blocks` (append one; optional `client_id` query param to set active dispatch client)
- `DELETE /watch-blocks/{id}`
- `GET /queue`
- `POST /queue/enqueue` (manual enqueue)
- `POST /queue/clear`
- `POST /queue/dispatch-next` (uses request `client_id`, else active client from watch-block setup, else env default)
