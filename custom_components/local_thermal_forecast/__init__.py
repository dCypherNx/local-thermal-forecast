"""Local Thermal Forecast integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_RETENTION_DAYS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)
from .coordinator import LocalThermalForecastCoordinator
from .open_meteo import OpenMeteoClient
from .storage import ThermalStore

PLATFORMS = [Platform.WEATHER]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class IntegrationRuntime:
    """Runtime objects owned by a config entry."""

    coordinator: LocalThermalForecastCoordinator
    storage: ThermalStore


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the config-entry-only integration domain."""
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

    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registry_entry.platform == DOMAIN and registry_entry.entity_id.startswith("sensor."):
            registry.async_remove(registry_entry.entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_config_entry_first_refresh()
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
