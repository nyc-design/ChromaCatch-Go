"""Audio source abstractions and implementations."""

from airplay_client.audio.base import AudioSource
from airplay_client.audio.factory import create_audio_source

__all__ = ["AudioSource", "create_audio_source"]
