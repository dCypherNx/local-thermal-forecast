"""One forecast entity for every selected source sensor."""

from __future__ import annotations

from hashlib import sha1
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.weather import Forecast, WeatherEntity, WeatherEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .const import DOMAIN, MODEL_NAMES, OUTDOOR_HOURS, ROOM_HOURS
from .coordinator import LocalThermalForecastCoordinator
from .observation import current_temperature

def _humidity_sensor_for_source(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    source_entity_id: str,
) -> str | None:
    """Return the unambiguous humidity sensor belonging to the source device."""
    source = registry.async_get(source_entity_id)
    if source is None or source.device_id is None:
        return None

    candidates: list[str] = []
    for entry in er.async_entries_for_device(registry, source.device_id):
        if entry.entity_id == source_entity_id:
            continue
        state = hass.states.get(entry.entity_id)
        if (
            state is not None
            and state.attributes.get("device_class") == SensorDeviceClass.HUMIDITY
        ):
            candidates.append(entry.entity_id)
    return candidates[0] if len(candidates) == 1 else None


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
                    _humidity_sensor_for_source(hass, registry, source_entity_id),
                )
            )
    entities.append(RawControlForecast(runtime.coordinator, entry))
    async_add_entities(entities)



class RawControlForecast(CoordinatorEntity[LocalThermalForecastCoordinator], WeatherEntity):
    """Untouched numerical-model forecast used as the experimental control."""

    _attr_has_entity_name = True
    _attr_name = "Controle ECMWF IFS HRES"
    _attr_icon = "mdi:weather-partly-cloudy"
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_pressure_unit = UnitOfPressure.HPA

    def __init__(
        self,
        coordinator: LocalThermalForecastCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_control_ecmwf_ifs"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Local Thermal Forecast",
            model="Hybrid weather and thermal model",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _current(self):
        return self.coordinator.data.control_current if self.coordinator.data else None

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.control_forecast)

    @property
    def native_temperature(self) -> float | None:
        return self._current.temperature if self._current else None

    @property
    def condition(self) -> str | None:
        return WMO_CONDITIONS.get(self._current.weather_code) if self._current else None

    @property
    def humidity(self) -> float | None:
        return self._current.humidity if self._current else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "forecast_role": "control",
            "forecast_horizon_hours": OUTDOOR_HOURS,
            "model": MODEL_NAMES["ecmwf_ifs"],
            "uses_local_observations": False,
            "issued_at": (
                self.coordinator.data.issued_at.isoformat() if self.coordinator.data else None
            ),
        }

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        if self.coordinator.data is None or not self.coordinator.data.control_forecast:
            return None
        forecasts: list[Forecast] = []
        for point in self.coordinator.data.control_forecast:
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
                "update raw control forecast listeners",
            )
        super()._handle_coordinator_update()


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
        humidity_entity_id: str | None,
    ) -> None:
        super().__init__(coordinator)
        self.source_entity_id = source_entity_id
        self.humidity_entity_id = humidity_entity_id
        self.role = role
        self._attr_unique_id = f"{entry.entry_id}_{role}_{stable_source_id}"
        self._attr_name = f"Previsão {source_name}"
        if role == "internal":
            self._attr_icon = "mdi:thermometer-lines"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Local Thermal Forecast",
            model="Hybrid weather and thermal model",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Refresh current observations as soon as their source sensors change."""
        await super().async_added_to_hass()
        tracked = [self.source_entity_id]
        if self.humidity_entity_id is not None:
            tracked.append(self.humidity_entity_id)
        self.async_on_remove(
            async_track_state_change_event(self.hass, tracked, self._handle_source_update)
        )

    @callback
    def _handle_source_update(self, _event: Event[EventStateChangedData]) -> None:
        """Write the latest local observation without waiting for forecast polling."""
        self.async_write_ha_state()

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
        """Return the live absolute temperature from the configured source sensor."""
        if self.hass is None:
            return None
        return current_temperature(self.hass, [self.source_entity_id])

    @property
    def condition(self) -> str | None:
        """Return the ambient weather condition required by WeatherEntity.

        Indoor forecasts use the home's outdoor condition. Their icon remains a
        thermometer, while providing a valid condition lets Home Assistant render
        the live room temperature instead of "unknown".
        """
        if self.coordinator.data is None:
            return None
        if self.role == "external":
            current = self.coordinator.data.external_current.get(self.source_entity_id)
        else:
            current = next(iter(self.coordinator.data.external_current.values()), None)
        return WMO_CONDITIONS.get(current.weather_code) if current else None

    @property
    def humidity(self) -> float | None:
        """Return live humidity from the source device when unambiguous."""
        if self.hass is None or self.humidity_entity_id is None:
            return None
        state = self.hass.states.get(self.humidity_entity_id)
        if state is None or state.state in {"unknown", "unavailable", "none", ""}:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "source_entity_id": self.source_entity_id,
            "forecast_role": self.role,
            "forecast_horizon_hours": OUTDOOR_HOURS if self.role == "external" else ROOM_HOURS,
        }
        if self.humidity_entity_id is not None:
            attributes["humidity_source_entity_id"] = self.humidity_entity_id
        if self.coordinator.data is not None:
            attributes["issued_at"] = self.coordinator.data.issued_at.isoformat()
            attributes["source_available"] = self.coordinator.data.source_available.get(
                self.source_entity_id, False
            )
        if self.role == "external" and self.coordinator.data is not None:
            models = self.coordinator.data.selected_models.get(self.source_entity_id, {})
            attributes["selected_models"] = {
                f"plus_{horizon}h": MODEL_NAMES.get(models.get(horizon), models.get(horizon))
                for horizon in (1, 6, 12, 24)
            }
            attributes["validation"] = {
                f"plus_{horizon}h": self._rounded_metrics(
                    self.coordinator.hybrid.metrics(self.source_entity_id, horizon)
                )
                for horizon in (1, 6, 12, 24)
            }
        elif self.coordinator.data is not None:
            attributes["validation"] = {
                f"plus_{horizon}h": self._rounded_metrics(
                    self.coordinator.hybrid.room_metrics(self.source_entity_id, horizon)
                )
                for horizon in (1, 3, 6, 12)
            }
        return attributes

    @staticmethod
    def _rounded_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        return {
            key: round(value, 3) if isinstance(value, float) else value
            for key, value in metrics.items()
        }

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
