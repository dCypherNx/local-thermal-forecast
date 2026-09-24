"""One forecast entity for every selected source sensor."""

from __future__ import annotations

from hashlib import sha1
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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .const import DOMAIN, MODEL_NAMES, OUTDOOR_HOURS, ROOM_HOURS
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
    """Create one weather forecast for every configured temperature sensor."""
    runtime: IntegrationRuntime = entry.runtime_data
    registry = er.async_get(hass)
    entities: list[WeatherEntity] = []
    for role, source_ids in (
        ("external", runtime.coordinator.external_sensors),
        ("internal", runtime.coordinator.room_sensors),
    ):
        for source_entity_id in source_ids:
            source = registry.async_get(source_entity_id)
            stable_source_id = (
                source.id if source else sha1(source_entity_id.encode()).hexdigest()[:16]
            )
            state = hass.states.get(source_entity_id)
            source_name = state.name if state else source_entity_id
            entities.append(
                SensorThermalForecast(
                    runtime.coordinator,
                    entry,
                    source_entity_id,
                    stable_source_id,
                    source_name,
                    role,
                )
            )
    async_add_entities(entities)


class SensorThermalForecast(CoordinatorEntity[LocalThermalForecastCoordinator], WeatherEntity):
    """Hourly forecast derived from one configured temperature sensor."""

    _attr_has_entity_name = True
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_pressure_unit = UnitOfPressure.HPA

    def __init__(
        self,
        coordinator: LocalThermalForecastCoordinator,
        entry: ConfigEntry,
        source_entity_id: str,
        stable_source_id: str,
        source_name: str,
        role: str,
    ) -> None:
        super().__init__(coordinator)
        self.source_entity_id = source_entity_id
        self.role = role
        self._attr_unique_id = f"{entry.entry_id}_{role}_{stable_source_id}"
        self._attr_name = f"Previsão {source_name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Local Thermal Forecast",
            model="Hybrid weather and thermal model",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _points(self) -> tuple:
        if self.coordinator.data is None:
            return ()
        if self.role == "external":
            return self.coordinator.data.external_forecasts.get(self.source_entity_id, ())
        return self.coordinator.data.room_forecasts.get(self.source_entity_id, ())

    @property
    def available(self) -> bool:
        """Keep the individual cached forecast available during provider failures."""
        return bool(self._points)

    @property
    def native_temperature(self) -> float | None:
        if self.coordinator.data is None:
            return None
        if self.role == "external":
            return self.coordinator.data.external_temperatures.get(self.source_entity_id)
        return self.coordinator.data.room_temperatures.get(self.source_entity_id)

    @property
    def condition(self) -> str | None:
        if self.role != "external" or not self._points:
            return None
        return WMO_CONDITIONS.get(self._points[0].weather_code)

    @property
    def humidity(self) -> float | None:
        if self.role != "external" or not self._points:
            return None
        return self._points[0].humidity

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "source_entity_id": self.source_entity_id,
            "forecast_role": self.role,
            "forecast_horizon_hours": OUTDOOR_HOURS if self.role == "external" else ROOM_HOURS,
        }
        if self.coordinator.data is not None:
            attributes["issued_at"] = self.coordinator.data.issued_at.isoformat()
        if self.role == "external" and self.coordinator.data is not None:
            models = self.coordinator.data.selected_models.get(self.source_entity_id, {})
            attributes["selected_models"] = {
                f"plus_{horizon}h": MODEL_NAMES.get(models.get(horizon), models.get(horizon))
                for horizon in (1, 6, 12, 24)
            }
        return attributes

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        """Return the source-specific series through Home Assistant's forecast API."""
        if not self._points:
            return None
        forecasts: list[Forecast] = []
        for point in self._points:
            forecast: Forecast = {
                "datetime": point.valid_at.isoformat(),
                "native_temperature": point.temperature,
            }
            if self.role == "external":
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
                forecast.update(
                    {key: value for key, value in optional.items() if value is not None}
                )
            forecasts.append(forecast)
        return forecasts

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.hass is not None:
            self.hass.async_create_task(
                self.async_update_listeners(("hourly",)),
                "update per-sensor thermal forecast listeners",
            )
        super()._handle_coordinator_update()
