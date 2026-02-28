"""Tests for the CLI entry point."""

import pytest

from airplay_client.cli import apply_cli_overrides, build_parser
from airplay_client.config import client_settings


class TestCLIParser:
    def test_connect_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["connect"])
        assert args.command == "connect"

    def test_run_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["run"])
        assert args.command == "run"

    def test_global_options_with_connect(self):
        parser = build_parser()
        args = parser.parse_args([
            "--backend-url", "ws://example.com/ws/client",
            "--api-key", "secret",
            "--esp32-host", "10.0.0.5",
            "--esp32-port", "8080",
            "connect",
        ])
        assert args.backend_url == "ws://example.com/ws/client"
        assert args.api_key == "secret"
        assert args.esp32_host == "10.0.0.5"
        assert args.esp32_port == 8080
        assert args.command == "connect"

    def test_global_options_with_run(self):
        parser = build_parser()
        args = parser.parse_args(["--backend-url", "ws://host/ws/client", "run"])
        assert args.backend_url == "ws://host/ws/client"
        assert args.command == "run"

    def test_defaults_are_none(self):
        parser = build_parser()
        args = parser.parse_args(["connect"])
        assert args.backend_url is None
        assert args.api_key is None
        assert args.esp32_host is None
        assert args.esp32_port is None
        assert args.audio_enabled is None
        assert args.audio_source is None

    def test_audio_flags_parse(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--audio-enabled",
                "false",
                "--audio-source",
                "system",
                "--audio-rate",
                "48000",
                "--audio-channels",
                "1",
                "--audio-input-backend",
                "pulse",
                "--audio-input-device",
                "default",
                "run",
            ]
        )
        assert args.audio_enabled == "false"
        assert args.audio_source == "system"
        assert args.audio_rate == 48000
        assert args.audio_channels == 1
        assert args.audio_input_backend == "pulse"
        assert args.audio_input_device == "default"

    def test_missing_subcommand_exits(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


class TestCLIOverrides:
    @pytest.fixture(autouse=True)
    def restore_audio_settings(self):
        original = (
            client_settings.audio_source,
            client_settings.audio_input_backend,
            client_settings.audio_input_device,
        )
        try:
            yield
        finally:
            (
                client_settings.audio_source,
                client_settings.audio_input_backend,
                client_settings.audio_input_device,
            ) = original

    def test_apply_audio_override_flags(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--audio-source",
                "system",
                "--audio-input-backend",
                "avfoundation",
                "--audio-input-device",
                ":1",
                "run",
            ]
        )
        apply_cli_overrides(args)
        assert client_settings.audio_source == "system"
        assert client_settings.audio_input_backend == "avfoundation"
        assert client_settings.audio_input_device == ":1"
