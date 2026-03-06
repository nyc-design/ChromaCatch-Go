"""Coordinate/expires parsing helpers for Discord payloads."""

from datetime import UTC, datetime, timedelta
import re

COORDS_RX = re.compile(r"(-?\d{1,3}\.\d+)[\s,\/]+(-?\d{1,3}\.\d+)")
DISCORD_TS_RX = re.compile(r"<t:(\d{9,11})(?::[tTdDfFR])?>")
DESPAWN_ABSOLUTE_RX = re.compile(
    r"(?:despawn(?:s)?|expire(?:s|d)?|until|time)\s*(?:at|:)?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?",
    re.IGNORECASE,
)
DESPAWN_RELATIVE_RX = re.compile(
    r"(?:despawn(?:s)?|expire(?:s|d)?)(?:\s+in)?|in",
    re.IGNORECASE,
)
DESPAWN_RELATIVE_VALUES_RX = re.compile(
    r"(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?\s*(?:(\d+)\s*s(?:ec(?:onds?)?)?)?",
    re.IGNORECASE,
)


def _is_valid_lat_lng(lat: float, lng: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def extract_coordinate(text: str) -> tuple[float, float] | None:
    if not text:
        return None

    match = COORDS_RX.search(text)
    if not match:
        return None

    lat = float(match.group(1))
    lng = float(match.group(2))
    if not _is_valid_lat_lng(lat, lng):
        return None
    return lat, lng


def parse_despawn_epoch(text: str, reference_time: datetime | None = None) -> float | None:
    """Parse despawn/expiry hints from message text into epoch seconds."""
    if not text:
        return None

    now = reference_time or datetime.now(UTC)

    # Prefer explicit Discord timestamps: <t:1738978123:R>
    ts_match = DISCORD_TS_RX.search(text)
    if ts_match:
        value = float(ts_match.group(1))
        # Handle accidental milliseconds payloads
        if value > 10_000_000_000:
            value = value / 1000.0
        return value

    # Relative countdown format
    rel_prefix_match = DESPAWN_RELATIVE_RX.search(text)
    if rel_prefix_match:
        tail = text[rel_prefix_match.end() :]
        rel_values = DESPAWN_RELATIVE_VALUES_RX.search(tail)
        if rel_values:
            hours = int(rel_values.group(1) or 0)
            minutes = int(rel_values.group(2) or 0)
            seconds = int(rel_values.group(3) or 0)
            if hours or minutes or seconds:
                return (now + timedelta(hours=hours, minutes=minutes, seconds=seconds)).timestamp()

    # Absolute time format (e.g., "despawn at 14:35")
    abs_match = DESPAWN_ABSOLUTE_RX.search(text)
    if abs_match:
        hour = int(abs_match.group(1))
        minute = int(abs_match.group(2))
        second = int(abs_match.group(3) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
            candidate = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
            # if parsed time already passed, assume next day
            if candidate < now - timedelta(minutes=1):
                candidate = candidate + timedelta(days=1)
            return candidate.timestamp()

    return None


def flatten_discord_message_parts(
    content: str | None,
    embeds: list[dict] | None,
    components: list[dict] | None,
) -> str:
    parts: list[str] = []

    if content:
        parts.append(content)

    for embed in embeds or []:
        if embed.get("title"):
            parts.append(str(embed["title"]))
        if embed.get("description"):
            parts.append(str(embed["description"]))

        for field in embed.get("fields") or []:
            if field.get("name"):
                parts.append(str(field["name"]))
            if field.get("value"):
                parts.append(str(field["value"]))

    for row in components or []:
        for component in row.get("components") or []:
            if component.get("label"):
                parts.append(str(component["label"]))
            if component.get("custom_id"):
                parts.append(str(component["custom_id"]))
            if component.get("url"):
                parts.append(str(component["url"]))

    return "\n".join(parts)
