"""Diagnostic sensors for each external forecast target."""

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
from .const import DOMAIN, MODEL_NAMES
from .coordinator import LocalThermalForecastCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up global and per-external-source diagnostics."""
    runtime: IntegrationRuntime = entry.runtime_data
    entities: list[SensorEntity] = [ForecastAgeSensor(runtime.coordinator, entry)]
    registry = er.async_get(hass)
    for entity_id in runtime.coordinator.external_sensors:
        source = registry.async_get(entity_id)
        stable_source_id = source.id if source else sha1(entity_id.encode()).hexdigest()[:16]
        state = hass.states.get(entity_id)
        source_name = state.name if state else entity_id
        entities.extend(
            (
                ModelQualitySensor(
                    runtime.coordinator, entry, entity_id, stable_source_id, source_name
                ),
                SelectedModelSensor(
                    runtime.coordinator, entry, entity_id, stable_source_id, source_name
                ),
                ErrorMetricSensor(
                    runtime.coordinator,
                    entry,
                    entity_id,
                    stable_source_id,
                    source_name,
                    "bias",
                    1,
                ),
            )
        )
        entities.extend(
            ErrorMetricSensor(
                runtime.coordinator,
                entry,
                entity_id,
                stable_source_id,
                source_name,
                "mae",
                horizon,
            )
            for horizon in (1, 6, 12, 24)
        )
    async_add_entities(entities)


class BaseDiagnosticSensor(CoordinatorEntity[LocalThermalForecastCoordinator], SensorEntity):
    """Base sensor tied to the integration service device."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: LocalThermalForecastCoordinator,
        entry: ConfigEntry,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

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


class SourceDiagnosticSensor(BaseDiagnosticSensor):
    """Diagnostic associated with one external source sensor."""

    def __init__(
        self,
        coordinator: LocalThermalForecastCoordinator,
        entry: ConfigEntry,
        source_entity_id: str,
        stable_source_id: str,
        suffix: str,
        source_name: str,
    ) -> None:
        super().__init__(coordinator, entry, f"{stable_source_id}_{suffix}")
        self.source_entity_id = source_entity_id
        self.source_name = source_name

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.data
            and self.source_entity_id in self.coordinator.data.external_forecasts
        )


class ErrorMetricSensor(SourceDiagnosticSensor):
    """Hybrid model bias or MAE for one source and horizon."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: LocalThermalForecastCoordinator,
        entry: ConfigEntry,
        source_entity_id: str,
        stable_source_id: str,
        source_name: str,
        metric: str,
        horizon: int,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            source_entity_id,
            stable_source_id,
            f"{metric}_{horizon}h",
            source_name,
        )
        self.metric = metric
        self.horizon = horizon
        self._attr_translation_key = f"forecast_{metric}"
        self._attr_translation_placeholders = {
            "source": source_name,
            "horizon": str(horizon),
        }

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.hybrid.metrics(self.source_entity_id, self.horizon).get(
            self.metric
        )
        return None if value is None else round(value, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "source_entity_id": self.source_entity_id,
            **self.coordinator.hybrid.metrics(self.source_entity_id, self.horizon),
        }


class ModelQualitySensor(SourceDiagnosticSensor):
    """Human-readable maturity of one source's +1h model."""

    _attr_translation_key = "model_quality"

    def __init__(
        self,
        coordinator: LocalThermalForecastCoordinator,
        entry: ConfigEntry,
        source_entity_id: str,
        stable_source_id: str,
        source_name: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            source_entity_id,
            stable_source_id,
            "model_quality",
            source_name,
        )
        self._attr_translation_placeholders = {"source": source_name}

    @property
    def native_value(self) -> str:
        metrics = self.coordinator.hybrid.metrics(self.source_entity_id, 1)
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
        return {
            "source_entity_id": self.source_entity_id,
            **self.coordinator.hybrid.metrics(self.source_entity_id, 1),
        }


class SelectedModelSensor(SourceDiagnosticSensor):
    """Selected weather model for one external source's next hour."""

    _attr_translation_key = "selected_model"

    def __init__(
        self,
        coordinator: LocalThermalForecastCoordinator,
        entry: ConfigEntry,
        source_entity_id: str,
        stable_source_id: str,
        source_name: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            source_entity_id,
            stable_source_id,
            "selected_model",
            source_name,
        )
        self._attr_translation_placeholders = {"source": source_name}

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        model = self.coordinator.data.selected_models.get(self.source_entity_id, {}).get(1)
        return MODEL_NAMES.get(model, model)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {"source_entity_id": self.source_entity_id}
        models = self.coordinator.data.selected_models.get(self.source_entity_id, {})
        return {
            "source_entity_id": self.source_entity_id,
            **{
                f"plus_{horizon}h": MODEL_NAMES.get(model, model)
                for horizon, model in models.items()
                if horizon in {1, 6, 12, 24}
            },
        }
