from datetime import UTC, datetime

from sniper_service.parser import extract_coordinate, parse_despawn_epoch


def test_extract_coordinate_from_text():
    text = "Spawn at 37.774900, -122.419400 now"
    coord = extract_coordinate(text)
    assert coord == (37.7749, -122.4194)


def test_extract_coordinate_invalid_range_returns_none():
    text = "coords 137.1234, -222.9999"
    assert extract_coordinate(text) is None


def test_extract_coordinate_missing_returns_none():
    assert extract_coordinate("No coordinates here") is None


def test_parse_despawn_epoch_discord_timestamp():
    epoch = parse_despawn_epoch("despawn <t:1738978123:R>")
    assert epoch == 1738978123


def test_parse_despawn_epoch_relative_time():
    now = datetime(2026, 3, 6, 22, 0, 0, tzinfo=UTC)
    epoch = parse_despawn_epoch("Despawns in 2m 30s", reference_time=now)
    assert epoch == now.timestamp() + 150
