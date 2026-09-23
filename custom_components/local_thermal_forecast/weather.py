"""Weather entity exposing the hybrid outdoor forecast."""

from __future__ import annotations

from typing import Any

from homeassistant.components.weather import Forecast, WeatherEntity, WeatherEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .const import DOMAIN
from .coordinator import LocalThermalForecastCoordinator

WMO_CONDITIONS = {
    0: "sunny",
    1: "sunny",
    2: "partlycloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "rainy",
    53: "rainy",
    55: "pouring",
    56: "rainy",
    57: "pouring",
    61: "rainy",
    63: "rainy",
    65: "pouring",
    66: "rainy",
    67: "pouring",
    71: "snowy",
    73: "snowy",
    75: "snowy-heavy",
    77: "snowy",
    80: "rainy",
    81: "rainy",
    82: "pouring",
    85: "snowy",
    86: "snowy-heavy",
    95: "lightning-rainy",
    96: "lightning-rainy",
    99: "lightning-rainy",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the home weather entity."""
    runtime: IntegrationRuntime = entry.runtime_data
    async_add_entities([LocalThermalWeather(runtime.coordinator, entry)])


class LocalThermalWeather(CoordinatorEntity[LocalThermalForecastCoordinator], WeatherEntity):
    """Hybrid observed and forecast weather for the home."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_pressure_unit = UnitOfPressure.HPA

    def __init__(self, coordinator: LocalThermalForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_home"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Local Thermal Forecast",
            model="Hybrid weather and thermal model",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Keep cached forecasts available while the provider is temporarily down."""
        return self.coordinator.data is not None

    @property
    def native_temperature(self) -> float | None:
        return self.coordinator.data.outdoor_temperature if self.coordinator.data else None

    @property
    def condition(self) -> str | None:
        if not self.coordinator.data or not self.coordinator.data.outdoor_forecast:
            return None
        return WMO_CONDITIONS.get(self.coordinator.data.outdoor_forecast[0].weather_code)

    @property
    def humidity(self) -> float | None:
        if not self.coordinator.data or not self.coordinator.data.outdoor_forecast:
            return None
        return self.coordinator.data.outdoor_forecast[0].humidity

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        """Return the cached 24-hour hybrid forecast through HA's forecast API."""
        if not self.coordinator.data:
            return None
        forecasts: list[Forecast] = []
        for point in self.coordinator.data.outdoor_forecast:
            forecast: Forecast = {
                "datetime": point.valid_at.isoformat(),
                "native_temperature": point.temperature,
            }
            optional: dict[str, Any] = {
                "condition": WMO_CONDITIONS.get(point.weather_code),
                "native_apparent_temperature": point.apparent_temperature,
                "humidity": point.humidity,
                "native_precipitation": point.precipitation,
                "precipitation_probability": point.precipitation_probability,
                "cloud_coverage": point.cloud_cover,
                "native_wind_speed": point.wind_speed,
                "native_wind_gust_speed": point.wind_gust,
            }
            forecast.update({key: value for key, value in optional.items() if value is not None})
            forecasts.append(forecast)
        return forecasts

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.hass is not None:
            self.hass.async_create_task(
                self.async_update_listeners(("hourly",)),
                "update local thermal forecast listeners",
            )
        super()._handle_coordinator_update()
