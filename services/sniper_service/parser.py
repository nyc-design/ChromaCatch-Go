"""Coordinate extraction/parsing helpers for Discord payloads."""

import re

COORDS_RX = re.compile(r"(-?\d{1,3}\.\d+)[\s,\/]+(-?\d{1,3}\.\d+)")


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
