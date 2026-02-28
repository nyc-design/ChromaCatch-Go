"""Root conftest - adds services paths so imports resolve."""

import sys
from pathlib import Path

_root = Path(__file__).parent

# backend and shared live directly under services/
sys.path.insert(0, str(_root / "services"))
# airplay_client package lives under services/airplay-client/
sys.path.insert(0, str(_root / "services" / "airplay-client"))
