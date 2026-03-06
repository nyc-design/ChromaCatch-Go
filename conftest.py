"""Root conftest - adds services paths so imports resolve."""

import sys
from pathlib import Path

_root = Path(__file__).parent

# Service packages (location_backend, sniper_service, shared) live under services/
sys.path.insert(0, str(_root / "services"))
# location_backend package lives under services/location_backend/
sys.path.insert(0, str(_root / "services" / "location_backend"))
# sniper_service package lives under services/sniper_service/
sys.path.insert(0, str(_root / "services" / "sniper_service"))
