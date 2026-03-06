"""Configuration for the location service."""

from pydantic_settings import BaseSettings


class LocationSettings(BaseSettings):
    api_key: str = ""
    host: str = "0.0.0.0"
    port: int = 8001

    model_config = {"env_file": ".env", "env_prefix": "CC_LOCATION_"}


location_settings = LocationSettings()
