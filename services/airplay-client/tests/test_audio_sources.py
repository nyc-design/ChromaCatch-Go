"""Tests for audio source factory and ffmpeg source command building."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from airplay_client.audio.airplay_audio_source import AirPlayAudioSource
from airplay_client.audio.factory import create_audio_source
from airplay_client.audio.ffmpeg_audio_source import FFmpegAudioSource
from airplay_client.config import client_settings


@pytest.fixture(autouse=True)
def restore_audio_settings():
    original = (
        client_settings.audio_enabled,
        client_settings.audio_source,
        client_settings.capture_source,
        client_settings.audio_input_backend,
        client_settings.audio_input_device,
    )
    try:
        yield
    finally:
        (
            client_settings.audio_enabled,
            client_settings.audio_source,
            client_settings.capture_source,
            client_settings.audio_input_backend,
            client_settings.audio_input_device,
        ) = original


def test_audio_factory_none_when_disabled():
    client_settings.audio_enabled = False
    assert create_audio_source() is None


def test_audio_factory_auto_airplay_mode():
    client_settings.audio_enabled = True
    client_settings.audio_source = "auto"
    client_settings.capture_source = "airplay"
    source = create_audio_source()
    assert isinstance(source, AirPlayAudioSource)


def test_audio_factory_auto_system_mode():
    client_settings.audio_enabled = True
    client_settings.audio_source = "auto"
    client_settings.capture_source = "capture"
    source = create_audio_source()
    assert isinstance(source, FFmpegAudioSource)


def test_audio_factory_system_mode():
    client_settings.audio_enabled = True
    client_settings.audio_source = "system"
    client_settings.capture_source = "screen"
    source = create_audio_source()
    assert isinstance(source, FFmpegAudioSource)


def test_audio_factory_airplay_requires_airplay_capture_source():
    client_settings.audio_enabled = True
    client_settings.audio_source = "airplay"
    client_settings.capture_source = "screen"
    with pytest.raises(ValueError, match="requires CC_CLIENT_CAPTURE_SOURCE='airplay'"):
        create_audio_source()


def test_audio_factory_invalid_mode():
    client_settings.audio_enabled = True
    client_settings.audio_source = "unknown"
    with pytest.raises(ValueError, match="Unsupported CC_CLIENT_AUDIO_SOURCE"):
        create_audio_source()


def test_ffmpeg_build_command_pulse_backend():
    source = FFmpegAudioSource(
        sample_rate=48000,
        channels=1,
        chunk_ms=20,
        input_backend="pulse",
        input_device="default",
    )
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        cmd = source._build_command()
    assert cmd[0] == "/usr/bin/ffmpeg"
    assert "-f" in cmd
    assert "pulse" in cmd
    assert "default" in cmd
    assert cmd[-1] == "pipe:1"


def test_ffmpeg_requires_device_for_dshow():
    source = FFmpegAudioSource(input_backend="dshow", input_device="")
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with pytest.raises(RuntimeError, match="audio_input_device is required"):
            source._build_command()
