"""Tests for the CLI entry point."""

import pytest

from airplay_client.cli import build_parser


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

    def test_missing_subcommand_exits(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])
