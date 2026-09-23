"""Local Thermal Forecast integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ATTR_ROOM_ENTITY_ID,
    CONF_RETENTION_DAYS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    SERVICE_GET_ROOM_FORECAST,
)
from .coordinator import LocalThermalForecastCoordinator
from .open_meteo import OpenMeteoClient
from .storage import ThermalStore

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.WEATHER, Platform.SENSOR]


@dataclass(slots=True)
class IntegrationRuntime:
    """Runtime objects owned by a config entry."""

    coordinator: LocalThermalForecastCoordinator
    storage: ThermalStore


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register integration-level actions."""

    async def async_get_room_forecast(call: ServiceCall) -> dict:
        entity_id: str = call.data[ATTR_ROOM_ENTITY_ID]
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime: IntegrationRuntime | None = getattr(entry, "runtime_data", None)
            if runtime and entity_id in runtime.coordinator.room_sensors:
                return runtime.coordinator.room_forecast_response(entity_id)
        return {"room_entity_id": entity_id, "forecast": []}

    if not hass.services.has_service(DOMAIN, SERVICE_GET_ROOM_FORECAST):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_ROOM_FORECAST,
            async_get_room_forecast,
            schema=vol.Schema({vol.Required(ATTR_ROOM_ENTITY_ID): cv.entity_id}),
            supports_response=SupportsResponse.ONLY,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Local Thermal Forecast from a config entry."""
    storage = ThermalStore(
        hass,
        entry.entry_id,
        entry.options.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS),
    )
    client = OpenMeteoClient(async_get_clientsession(hass))
    coordinator = LocalThermalForecastCoordinator(hass, entry, client, storage)
    await coordinator.async_initialize()

    entry.runtime_data = IntegrationRuntime(coordinator, storage)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(
        hass,
        coordinator.async_request_refresh(),
        "refresh local thermal forecast",
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and flush learned state."""
    runtime: IntegrationRuntime = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await runtime.storage.async_save()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after options or reconfiguration changes."""
    await hass.config_entries.async_reload(entry.entry_id)
