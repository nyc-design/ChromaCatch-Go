"""Coordinate/expires parsing helpers for Discord payloads."""

from datetime import UTC, datetime, timedelta
import re

COORDS_RX = re.compile(r"(-?\d{1,3}\.\d+)[\s,\/]+(-?\d{1,3}\.\d+)")
DISCORD_TS_RX = re.compile(r"<t:(\d{9,11})(?::[tTdDfFR])?>")
DESPAWN_TIMER_PAREN_RX = re.compile(r"\((\d{1,2}):(\d{2})\)")
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
LEVEL_RX = re.compile(r"(?:\bL|\bLvl|\bLevel)\s*[: ]?\s*(\d{1,2})\b", re.IGNORECASE)
CP_RX = re.compile(r"\bCP\s*[: ]?\s*(\d{2,5})\b", re.IGNORECASE)
IV_PCT_A_RX = re.compile(r"\bIVs?\s*[: ]*([0-9]{1,3}(?:\.[0-9]+)?)%", re.IGNORECASE)
IV_PCT_B_RX = re.compile(r"([0-9]{1,3}(?:\.[0-9]+)?)%\s*IVs?\b", re.IGNORECASE)
IV_BREAKDOWN_RX = re.compile(r"\((\d{1,2})/(\d{1,2})/(\d{1,2})\)")
EMOJI_LEVEL_RX = re.compile(r"<:Lv:\d+>\s*(\d{1,2})", re.IGNORECASE)
EMOJI_CP_RX = re.compile(r"<:Cp:\d+>\s*(\d{2,5})", re.IGNORECASE)
EMOJI_IV_RX = re.compile(r"<:Iv:\d+>\s*([0-9]{1,3}(?:\.[0-9]+)?)", re.IGNORECASE)
POKEMON_LABEL_RX = re.compile(
    r"(?:pokemon|pokémon|name)\s*[:\-]\s*([A-Za-z][A-Za-z .'\-]{1,40})",
    re.IGNORECASE,
)
POKEMON_BOLD_RX = re.compile(r"\*\*([A-Za-z][A-Za-z .'\-]{1,40})\*\*")
POKEMON_NEAR_CP_RX = re.compile(
    r"\b([A-Z][a-z]+(?:[\s-][A-Z][a-z]+){0,2})\b(?:\s*[|•\-—]\s*|\s+)?CP\s*[: ]?\d{2,5}\b"
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
    timer_paren = DESPAWN_TIMER_PAREN_RX.search(text)
    if timer_paren:
        minutes = int(timer_paren.group(1) or 0)
        seconds = int(timer_paren.group(2) or 0)
        if minutes or seconds:
            return (now + timedelta(minutes=minutes, seconds=seconds)).timestamp()

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


def _compute_iv_pct(iv_atk: int | None, iv_def: int | None, iv_sta: int | None) -> float | None:
    if iv_atk is None or iv_def is None or iv_sta is None:
        return None
    total = iv_atk + iv_def + iv_sta
    return round((total / 45.0) * 100.0, 1)


def _normalize_pokemon_name(raw_name: str | None) -> str | None:
    if raw_name is None:
        return None
    name = re.sub(r"\s+", " ", raw_name).strip(" -|•—:\n\t")
    if not name:
        return None
    if len(name) > 60:
        name = name[:60].rstrip()
    return name


def parse_spawn_metadata(text: str) -> dict:
    """Parse Pokémon metadata fields from Discord message text."""
    metadata: dict = {
        "pokemon_name": None,
        "level": None,
        "cp": None,
        "iv_pct": None,
        "iv_atk": None,
        "iv_def": None,
        "iv_sta": None,
    }
    if not text:
        return metadata

    level_match = LEVEL_RX.search(text) or EMOJI_LEVEL_RX.search(text)
    cp_match = CP_RX.search(text) or EMOJI_CP_RX.search(text)
    iv_pct_match = IV_PCT_A_RX.search(text) or IV_PCT_B_RX.search(text) or EMOJI_IV_RX.search(text)
    iv_breakdown = IV_BREAKDOWN_RX.search(text)

    if level_match:
        metadata["level"] = int(level_match.group(1))
    if cp_match:
        metadata["cp"] = int(cp_match.group(1))
    if iv_pct_match:
        metadata["iv_pct"] = float(iv_pct_match.group(1))
    if iv_breakdown:
        metadata["iv_atk"] = int(iv_breakdown.group(1))
        metadata["iv_def"] = int(iv_breakdown.group(2))
        metadata["iv_sta"] = int(iv_breakdown.group(3))

    if metadata["iv_pct"] is None:
        metadata["iv_pct"] = _compute_iv_pct(
            metadata["iv_atk"],
            metadata["iv_def"],
            metadata["iv_sta"],
        )

    pokemon_name_match = (
        POKEMON_LABEL_RX.search(text)
        or POKEMON_NEAR_CP_RX.search(text)
        or POKEMON_BOLD_RX.search(text)
    )
    if pokemon_name_match:
        metadata["pokemon_name"] = _normalize_pokemon_name(pokemon_name_match.group(1))

    return metadata


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
