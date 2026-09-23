"""Privacy-conscious diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from . import IntegrationRuntime
from .const import CONF_EXTERNAL_SENSORS, CONF_ROOM_SENSORS

TO_REDACT = {"latitude", "longitude", CONF_EXTERNAL_SENSORS, CONF_ROOM_SENSORS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without exact location or household entity IDs."""
    runtime: IntegrationRuntime = entry.runtime_data
    coordinator = runtime.coordinator
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "last_exception": str(coordinator.last_exception) if coordinator.last_exception else None,
        "has_cached_data": coordinator.data is not None,
        "snapshot_count": len(runtime.storage.snapshots),
        "configured_external_sensor_count": len(coordinator.external_sensors),
        "configured_room_count": len(coordinator.room_sensors),
        "metrics": {
            str(horizon): coordinator.hybrid.metrics(horizon) for horizon in (1, 6, 12, 24)
        },
    }
