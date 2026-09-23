"""Diagnostic and room summary sensors."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha1
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .const import MODEL_NAMES
from .coordinator import LocalThermalForecastCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up diagnostic and per-room summary sensors."""
    runtime: IntegrationRuntime = entry.runtime_data
    entities: list[SensorEntity] = [
        ForecastAgeSensor(runtime.coordinator, entry),
        ModelQualitySensor(runtime.coordinator, entry),
        SelectedModelSensor(runtime.coordinator, entry),
        ErrorMetricSensor(runtime.coordinator, entry, "bias", 1),
    ]
    entities.extend(
        ErrorMetricSensor(runtime.coordinator, entry, "mae", horizon) for horizon in (1, 6, 12, 24)
    )
    registry = er.async_get(hass)
    for entity_id in runtime.coordinator.room_sensors:
        source = registry.async_get(entity_id)
        stable_source_id = source.id if source else sha1(entity_id.encode()).hexdigest()[:16]
        entities.append(
            RoomForecastSensor(
                runtime.coordinator,
                entry,
                entity_id,
                stable_source_id,
                hass.states.get(entity_id).name if hass.states.get(entity_id) else entity_id,
            )
        )
    async_add_entities(entities)


class BaseDiagnosticSensor(CoordinatorEntity[LocalThermalForecastCoordinator], SensorEntity):
    """Base sensor tied to the integration service device."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: LocalThermalForecastCoordinator, entry: ConfigEntry, suffix: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = {"identifiers": {("local_thermal_forecast", entry.entry_id)}}

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class ForecastAgeSensor(BaseDiagnosticSensor):
    """Age of the last valid provider response."""

    _attr_translation_key = "forecast_age"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LocalThermalForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "forecast_age")

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return round(
            max(0.0, (datetime.now(UTC) - self.coordinator.data.issued_at).total_seconds() / 60),
            1,
        )


class ErrorMetricSensor(BaseDiagnosticSensor):
    """Hybrid model bias or MAE at one horizon."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: LocalThermalForecastCoordinator,
        entry: ConfigEntry,
        metric: str,
        horizon: int,
    ) -> None:
        super().__init__(coordinator, entry, f"{metric}_{horizon}h")
        self.metric = metric
        self.horizon = horizon
        self._attr_translation_key = f"forecast_{metric}"
        self._attr_translation_placeholders = {"horizon": str(horizon)}

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.hybrid.metrics(self.horizon).get(self.metric)
        return None if value is None else round(value, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.hybrid.metrics(self.horizon)


class ModelQualitySensor(BaseDiagnosticSensor):
    """Human-readable maturity of the +1h hybrid model."""

    _attr_translation_key = "model_quality"

    def __init__(self, coordinator: LocalThermalForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "model_quality")

    @property
    def native_value(self) -> str:
        metrics = self.coordinator.hybrid.metrics(1)
        count = metrics["count"]
        mae = metrics["mae"]
        if count < 8:
            return "learning"
        if count < 30 or mae is None:
            return "low"
        if mae <= 1.0:
            return "high"
        if mae <= 2.0:
            return "medium"
        return "low"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.hybrid.metrics(1)


class SelectedModelSensor(BaseDiagnosticSensor):
    """Selected weather model for the next hour."""

    _attr_translation_key = "selected_model"

    def __init__(self, coordinator: LocalThermalForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "selected_model")

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        model = self.coordinator.data.selected_models.get(1)
        return MODEL_NAMES.get(model, model)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        return {
            f"plus_{horizon}h": MODEL_NAMES.get(model, model)
            for horizon, model in self.coordinator.data.selected_models.items()
            if horizon in {1, 6, 12, 24}
        }


class RoomForecastSensor(CoordinatorEntity[LocalThermalForecastCoordinator], SensorEntity):
    """Compact summary of one independent room forecast."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: LocalThermalForecastCoordinator,
        entry: ConfigEntry,
        source_entity_id: str,
        stable_source_id: str,
        source_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.source_entity_id = source_entity_id
        self._attr_unique_id = f"{entry.entry_id}_room_{stable_source_id}"
        self._attr_name = f"{source_name} previsão +1h"
        self._attr_device_info = {"identifiers": {("local_thermal_forecast", entry.entry_id)}}

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data and self.source_entity_id in self.coordinator.data.room_forecasts
        )

    @property
    def native_value(self) -> float | None:
        if not self.available:
            return None
        return self.coordinator.data.room_forecasts[self.source_entity_id][0].temperature

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.available:
            return {"source_entity_id": self.source_entity_id}
        points = self.coordinator.data.room_forecasts[self.source_entity_id]
        return {
            "source_entity_id": self.source_entity_id,
            "forecast_3h": points[2].temperature,
            "forecast_6h": points[5].temperature,
            "forecast_12h": points[11].temperature,
            "issued_at": self.coordinator.data.issued_at.isoformat(),
        }
