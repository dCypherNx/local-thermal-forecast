"""Constants for Local Thermal Forecast."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "local_thermal_forecast"
CONF_EXTERNAL_SENSORS: Final = "external_sensors"
CONF_ROOM_SENSORS: Final = "room_sensors"
CONF_RETENTION_DAYS: Final = "retention_days"
CONF_UPDATE_INTERVAL: Final = "update_interval_minutes"

DEFAULT_NAME: Final = "Local Thermal Forecast"
DEFAULT_RETENTION_DAYS: Final = 90
DEFAULT_UPDATE_INTERVAL_MINUTES: Final = 60
MIN_UPDATE_INTERVAL_MINUTES: Final = 30
MAX_UPDATE_INTERVAL_MINUTES: Final = 360

OUTDOOR_HOURS: Final = 24
ROOM_HOURS: Final = 12
HISTORY_TOLERANCE: Final = timedelta(hours=2)

PRIMARY_MODEL: Final = "ecmwf_ifs"
MODEL_IDS: Final = (
    PRIMARY_MODEL,
    "best_match",
    "icon_global",
    "ncep_gfs_global",
)
MODEL_NAMES: Final = {
    "ecmwf_ifs": "ECMWF IFS HRES",
    "best_match": "Open-Meteo Best Match",
    "icon_global": "DWD ICON Global",
    "ncep_gfs_global": "NCEP GFS Global",
}

STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}.entry"
