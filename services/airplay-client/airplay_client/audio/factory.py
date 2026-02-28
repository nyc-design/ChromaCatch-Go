"""Factory for selecting the configured audio source."""

from __future__ import annotations

from airplay_client.audio.airplay_audio_source import AirPlayAudioSource
from airplay_client.audio.base import AudioSource
from airplay_client.audio.ffmpeg_audio_source import FFmpegAudioSource
from airplay_client.config import client_settings as settings


def _resolve_audio_mode() -> str:
    mode = settings.audio_source.lower().strip()
    if mode == "auto":
        return "airplay" if settings.capture_source.lower().strip() == "airplay" else "system"
    return mode


def create_audio_source() -> AudioSource | None:
    if not settings.audio_enabled:
        return None

    mode = _resolve_audio_mode()
    if mode == "none":
        return None
    if mode == "airplay":
        if settings.capture_source.lower().strip() != "airplay":
            raise ValueError(
                "CC_CLIENT_AUDIO_SOURCE='airplay' requires CC_CLIENT_CAPTURE_SOURCE='airplay'."
            )
        return AirPlayAudioSource()
    if mode == "system":
        return FFmpegAudioSource()

    raise ValueError(
        f"Unsupported CC_CLIENT_AUDIO_SOURCE='{settings.audio_source}'. "
        "Use one of: auto, airplay, system, none."
    )
