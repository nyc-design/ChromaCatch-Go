"""Configuration for the Discord sniper service."""

from pydantic_settings import BaseSettings


class SniperSettings(BaseSettings):
    api_host: str = "0.0.0.0"
    api_port: int = 8010

    # Discord self-client token (user token). Keep empty to disable monitor loop.
    discord_token: str = ""

    # Location backend dispatch defaults
    location_post_url: str = "http://location-backend:8001/location"
    location_client_id: str = "ios-app"
    location_altitude: float = 10.0
    location_speed_knots: float = 0.0
    location_heading: float = 0.0

    # Queue + persistence
    queue_max: int = 500
    watch_blocks_path: str = "/tmp/sniper_watch_blocks.json"

    model_config = {"env_file": ".env", "env_prefix": "CC_SNIPER_"}


sniper_settings = SniperSettings()
