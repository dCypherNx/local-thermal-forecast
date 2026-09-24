"""Privacy-conscious diagnostics and forecast-validation export."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from . import IntegrationRuntime
from .const import CONF_EXTERNAL_SENSORS, CONF_ROOM_SENSORS

TO_REDACT = {"latitude", "longitude", CONF_EXTERNAL_SENSORS, CONF_ROOM_SENSORS}


def _anonymize_snapshots(
    snapshots: list[dict[str, Any]],
    external_sensors: list[str],
    room_sensors: list[str],
) -> list[dict[str, Any]]:
    """Return the validation ledger with household entity IDs replaced by stable aliases."""
    aliases = {
        **{entity_id: f"external_{index}" for index, entity_id in enumerate(external_sensors, 1)},
        **{entity_id: f"room_{index}" for index, entity_id in enumerate(room_sensors, 1)},
    }
    exported = deepcopy(snapshots)
    for snapshot in exported:
        for group in ("external", "rooms"):
            values = snapshot.get(group)
            if not isinstance(values, dict):
                continue
            snapshot[group] = {
                aliases.get(entity_id, f"source_{index}"): payload
                for index, (entity_id, payload) in enumerate(values.items(), 1)
            }
    return exported


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return an anonymized, auditable dataset suitable for model evaluation."""
    runtime: IntegrationRuntime = entry.runtime_data
    coordinator = runtime.coordinator
    snapshots = _anonymize_snapshots(
        runtime.storage.snapshots,
        coordinator.external_sensors,
        coordinator.room_sensors,
    )
    issued = [snapshot.get("issued_at") for snapshot in snapshots if snapshot.get("issued_at")]
    return {
        "export": {
            "format": "local_thermal_forecast_validation",
            "schema_version": 1,
            "integration_version": "0.3.4-rc.1",
            "period_start": min(issued) if issued else None,
            "period_end": max(issued) if issued else None,
            "snapshot_count": len(snapshots),
            "update_interval_minutes": coordinator.update_interval.total_seconds() / 60,
            "timezone": str(hass.config.time_zone),
        },
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "last_exception": str(coordinator.last_exception) if coordinator.last_exception else None,
        "has_cached_data": coordinator.data is not None,
        "configured_external_sensor_count": len(coordinator.external_sensors),
        "configured_room_count": len(coordinator.room_sensors),
        "external_metrics": {
            f"external_{index}": {
                str(horizon): coordinator.hybrid.metrics(entity_id, horizon)
                for horizon in (1, 6, 12, 24)
            }
            for index, entity_id in enumerate(coordinator.external_sensors, start=1)
        },
        "room_metrics": {
            f"room_{index}": {
                str(horizon): coordinator.hybrid.room_metrics(entity_id, horizon)
                for horizon in (1, 3, 6, 12)
            }
            for index, entity_id in enumerate(coordinator.room_sensors, start=1)
        },
        "forecast_ledger": snapshots,
    }
