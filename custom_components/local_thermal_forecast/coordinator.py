"""Coordinator for weather collection, verification and hybrid forecasts."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_EXTERNAL_SENSORS,
    CONF_ROOM_SENSORS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    HISTORY_TOLERANCE,
    OUTDOOR_HOURS,
    ROOM_HOURS,
)
from .hybrid import HybridSystem
from .models import CoordinatorData, HybridForecastPoint, RoomForecastPoint
from .observation import (
    async_temperature_history,
    current_temperature,
    nearest_temperature,
    temperature_slope,
)
from .open_meteo import resample_forecast
from .provider import ForecastProvider, ForecastProviderError
from .storage import ThermalStore

_LOGGER = logging.getLogger(__name__)


class LocalThermalForecastCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinate all data and learning for one home."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ForecastProvider,
        storage: ThermalStore,
    ) -> None:
        interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            config_entry=entry,
            update_interval=timedelta(minutes=interval),
        )
        self.entry = entry
        self.client = client
        self.storage = storage
        self.hybrid = HybridSystem()

    @property
    def external_sensors(self) -> list[str]:
        return list(self.entry.data[CONF_EXTERNAL_SENSORS])

    @property
    def room_sensors(self) -> list[str]:
        return list(self.entry.data.get(CONF_ROOM_SENSORS, []))

    @property
    def all_sensors(self) -> list[str]:
        """Return every forecast target without imposing an artificial limit."""
        return list(dict.fromkeys(self.external_sensors + self.room_sensors))

    async def async_initialize(self) -> None:
        """Restore the learned model and last usable forecast."""
        stored = await self.storage.async_load()
        self.hybrid = HybridSystem.from_dict(stored.get("hybrid_state"))
        if last_data := stored.get("last_data"):
            self.async_set_updated_data(CoordinatorData.from_dict(last_data))

    async def _async_update_data(self) -> CoordinatorData:
        now = datetime.now(UTC)
        latitude = float(self.entry.data["latitude"])
        longitude = float(self.entry.data["longitude"])

        recent_history = await async_temperature_history(
            self.hass, self.all_sensors, now - timedelta(hours=3), now
        )
        if await self._async_validate_pending(now, recent_history):
            self.storage.async_delay_save()

        try:
            bundle = await self.client.async_fetch(latitude, longitude)
        except ForecastProviderError as err:
            raise UpdateFailed(str(err)) from err

        forecasts = {
            model: resample_forecast(forecast, bundle.retrieved_at)
            for model, forecast in bundle.forecasts.items()
        }
        primary = next(iter(forecasts.values()))
        external_temperatures: dict[str, float] = {}
        external_current: dict[str, HybridForecastPoint] = {}
        external_slopes: dict[str, float] = {}
        external_forecasts: dict[str, tuple[HybridForecastPoint, ...]] = {}
        external_candidates: dict[str, dict[str, list[float]]] = {}
        selected_models: dict[str, dict[int, str]] = {}
        source_available: dict[str, bool] = {}

        for entity_id in self.external_sensors:
            observed = current_temperature(self.hass, [entity_id])
            source_available[entity_id] = observed is not None
            if observed is None:
                observed = primary.points[0].temperature
            slope = temperature_slope(recent_history, [entity_id])
            external_temperatures[entity_id] = observed
            external_slopes[entity_id] = slope

            candidates: dict[str, list[float]] = {}
            for model, forecast in forecasts.items():
                raw_now = forecast.points[0].temperature
                candidates[model] = [
                    self.hybrid.predict_outdoor(
                        entity_id,
                        model,
                        horizon,
                        observed,
                        self.hybrid.outdoor_features(
                            raw_now,
                            forecast.points[horizon].temperature,
                            slope,
                            horizon,
                        ),
                    )
                    for horizon in range(1, OUTDOOR_HOURS + 1)
                ]
            external_candidates[entity_id] = candidates

            entity_models: dict[int, str] = {}
            points: list[HybridForecastPoint] = []
            for horizon in range(1, OUTDOOR_HOURS + 1):
                selected = self.hybrid.select_model(entity_id, horizon, set(forecasts))
                entity_models[horizon] = selected
                source = forecasts[selected].points[horizon]
                points.append(
                    HybridForecastPoint(
                        valid_at=source.valid_at,
                        temperature=round(candidates[selected][horizon - 1], 2),
                        raw_temperature=source.temperature,
                        model=selected,
                        apparent_temperature=source.apparent_temperature,
                        humidity=source.humidity,
                        precipitation=source.precipitation,
                        precipitation_probability=source.precipitation_probability,
                        weather_code=source.weather_code,
                        cloud_cover=source.cloud_cover,
                        wind_speed=source.wind_speed,
                        wind_gust=source.wind_gust,
                    )
                )
            selected_models[entity_id] = entity_models
            external_forecasts[entity_id] = tuple(points)
            current_source = forecasts[entity_models[1]].points[0]
            external_current[entity_id] = HybridForecastPoint(
                valid_at=current_source.valid_at,
                temperature=round(observed, 2),
                raw_temperature=current_source.temperature,
                model=entity_models[1],
                apparent_temperature=current_source.apparent_temperature,
                humidity=current_source.humidity,
                precipitation=current_source.precipitation,
                precipitation_probability=current_source.precipitation_probability,
                weather_code=current_source.weather_code,
                cloud_cover=current_source.cloud_cover,
                wind_speed=current_source.wind_speed,
                wind_gust=current_source.wind_gust,
            )

        outdoor_now = median(external_temperatures.values())
        outdoor_series = [
            median(points[horizon].temperature for points in external_forecasts.values())
            for horizon in range(OUTDOOR_HOURS)
        ]
        radiation = [
            primary.points[horizon].shortwave_radiation for horizon in range(1, OUTDOOR_HOURS + 1)
        ]

        room_temperatures: dict[str, float] = {}
        room_slopes: dict[str, float] = {}
        room_forecasts: dict[str, tuple[RoomForecastPoint, ...]] = {}
        room_prediction_arrays: dict[str, list[float]] = {}
        for entity_id in self.room_sensors:
            room_temperature = current_temperature(self.hass, [entity_id])
            source_available[entity_id] = room_temperature is not None
            if room_temperature is None:
                room_temperature = self.data.room_temperatures.get(entity_id) if self.data else None
            if room_temperature is None:
                continue
            room_temperatures[entity_id] = room_temperature
            room_slopes[entity_id] = temperature_slope(recent_history, [entity_id])
            values: list[float] = []
            points: list[RoomForecastPoint] = []
            for horizon in range(1, ROOM_HOURS + 1):
                features = self.hybrid.room_features(
                    outdoor_now,
                    outdoor_series[horizon - 1],
                    room_slopes[entity_id],
                    radiation[horizon - 1],
                    horizon,
                )
                predicted = self.hybrid.predict_room(entity_id, horizon, room_temperature, features)
                values.append(predicted)
                points.append(
                    RoomForecastPoint(primary.points[horizon].valid_at, round(predicted, 2))
                )
            room_prediction_arrays[entity_id] = values
            room_forecasts[entity_id] = tuple(points)

        control_current = None
        control_forecast: tuple[HybridForecastPoint, ...] = ()
        if self.external_sensors:
            control_entity_id = self.external_sensors[0]
            control_models = selected_models.get(control_entity_id, {})
            if control_models:
                current_model = control_models[1]
                current_source = forecasts[current_model].points[0]
                control_current = HybridForecastPoint(
                    valid_at=current_source.valid_at,
                    temperature=current_source.temperature,
                    raw_temperature=current_source.temperature,
                    model=current_model,
                    apparent_temperature=current_source.apparent_temperature,
                    humidity=current_source.humidity,
                    precipitation=current_source.precipitation,
                    precipitation_probability=current_source.precipitation_probability,
                    weather_code=current_source.weather_code,
                    cloud_cover=current_source.cloud_cover,
                    wind_speed=current_source.wind_speed,
                    wind_gust=current_source.wind_gust,
                )
                control_points: list[HybridForecastPoint] = []
                for horizon in range(1, OUTDOOR_HOURS + 1):
                    model = control_models[horizon]
                    source = forecasts[model].points[horizon]
                    control_points.append(
                        HybridForecastPoint(
                            valid_at=source.valid_at,
                            temperature=source.temperature,
                            raw_temperature=source.temperature,
                            model=model,
                            apparent_temperature=source.apparent_temperature,
                            humidity=source.humidity,
                            precipitation=source.precipitation,
                            precipitation_probability=source.precipitation_probability,
                            weather_code=source.weather_code,
                            cloud_cover=source.cloud_cover,
                            wind_speed=source.wind_speed,
                            wind_gust=source.wind_gust,
                        )
                    )
                control_forecast = tuple(control_points)

        data = CoordinatorData(
            issued_at=bundle.retrieved_at,
            external_temperatures=external_temperatures,
            control_current=control_current,
            control_forecast=control_forecast,
            external_current=external_current,
            external_forecasts=external_forecasts,
            room_temperatures=room_temperatures,
            room_forecasts=room_forecasts,
            selected_models=selected_models,
            source_available=source_available,
        )
        self._append_snapshot(
            data,
            forecasts,
            external_candidates,
            external_slopes,
            room_slopes,
            room_prediction_arrays,
            outdoor_now,
            outdoor_series,
            radiation,
        )
        self.storage.data["hybrid_state"] = self.hybrid.to_dict()
        self.storage.data["last_data"] = data.as_dict()
        self.storage.async_delay_save()
        return data

    def _append_snapshot(
        self,
        data: CoordinatorData,
        forecasts: dict[str, Any],
        external_candidates: dict[str, dict[str, list[float]]],
        external_slopes: dict[str, float],
        room_slopes: dict[str, float],
        room_predictions: dict[str, list[float]],
        outdoor_now: float,
        outdoor_series: list[float],
        radiation: list[float | None],
    ) -> None:
        """Append an auditable issued forecast for every selected sensor."""
        valid_at = [
            point.valid_at.isoformat() for point in next(iter(data.external_forecasts.values()))
        ]
        self.storage.snapshots.append(
            {
                "purpose": "temperature_forecast_per_sensor",
                "gateway": "open_meteo",
                "issued_at": data.issued_at.isoformat(),
                "valid_at": valid_at,
                "models": {
                    model: {
                        "provider_run_at": None,
                        "raw_now": forecast.points[0].temperature,
                        "raw": [
                            forecast.points[horizon].temperature
                            for horizon in range(1, OUTDOOR_HOURS + 1)
                        ],
                    }
                    for model, forecast in forecasts.items()
                },
                "external": {
                    entity_id: {
                        "base": data.external_temperatures[entity_id],
                        "slope": external_slopes[entity_id],
                        "source_available": data.source_available[entity_id],
                        "hybrid_by_model": external_candidates[entity_id],
                        "raw_error": {model: [None] * OUTDOOR_HOURS for model in forecasts},
                        "hybrid_error": {model: [None] * OUTDOOR_HOURS for model in forecasts},
                        "selected": [
                            data.selected_models[entity_id][horizon]
                            for horizon in range(1, OUTDOOR_HOURS + 1)
                        ],
                        "hybrid": [
                            point.temperature for point in data.external_forecasts[entity_id]
                        ],
                        "observed": [None] * OUTDOOR_HOURS,
                        "validated": [False] * OUTDOOR_HOURS,
                    }
                    for entity_id in data.external_forecasts
                },
                "outdoor_now": outdoor_now,
                "outdoor": outdoor_series,
                "radiation": radiation,
                "rooms": {
                    entity_id: {
                        "base": data.room_temperatures[entity_id],
                        "slope": room_slopes[entity_id],
                        "source_available": data.source_available[entity_id],
                        "hybrid": predictions,
                        "observed": [None] * ROOM_HOURS,
                        "error": [None] * ROOM_HOURS,
                        "validated": [False] * ROOM_HOURS,
                    }
                    for entity_id, predictions in room_predictions.items()
                },
            }
        )

    async def _async_validate_pending(
        self,
        now: datetime,
        recent_history: dict[str, list[tuple[datetime, float]]],
    ) -> bool:
        """Match every issued per-sensor forecast to Recorder observations."""
        pending_targets: list[datetime] = []
        for snapshot in self.storage.snapshots:
            if snapshot.get("compact"):
                continue
            for index, value in enumerate(snapshot["valid_at"]):
                target = datetime.fromisoformat(value)
                external_pending = any(
                    not values["validated"][index]
                    for values in snapshot.get("external", {}).values()
                )
                room_pending = index < ROOM_HOURS and any(
                    not values["validated"][index] for values in snapshot.get("rooms", {}).values()
                )
                if (external_pending or room_pending) and target <= now - timedelta(minutes=5):
                    pending_targets.append(target)
        if not pending_targets:
            return False

        start = min(pending_targets) - HISTORY_TOLERANCE
        history = recent_history
        if start < now - timedelta(hours=3):
            history = await async_temperature_history(self.hass, self.all_sensors, start, now)
        tolerance = HISTORY_TOLERANCE.total_seconds()
        changed = False

        for snapshot in self.storage.snapshots:
            if snapshot.get("compact"):
                continue
            for index, value in enumerate(snapshot["valid_at"]):
                target = datetime.fromisoformat(value)
                if target > now - timedelta(minutes=5):
                    continue
                horizon = index + 1
                for entity_id, values in snapshot.get("external", {}).items():
                    if values["validated"][index]:
                        continue
                    observed = nearest_temperature(history, [entity_id], target, tolerance)
                    if observed is not None:
                        values["observed"][index] = observed
                        for model, model_values in snapshot["models"].items():
                            features = self.hybrid.outdoor_features(
                                model_values["raw_now"],
                                model_values["raw"][index],
                                values["slope"],
                                horizon,
                            )
                            self.hybrid.update_outdoor(
                                entity_id,
                                model,
                                horizon,
                                features,
                                values["base"],
                                model_values["raw"][index],
                                values["hybrid_by_model"][model][index],
                                observed,
                            )
                            values["raw_error"][model][index] = round(
                                model_values["raw"][index] - observed, 3
                            )
                            values["hybrid_error"][model][index] = round(
                                values["hybrid_by_model"][model][index] - observed, 3
                            )
                    if observed is not None or now - target > timedelta(hours=2):
                        values["validated"][index] = True
                        changed = True

                if index >= ROOM_HOURS:
                    continue
                for entity_id, values in snapshot.get("rooms", {}).items():
                    if values["validated"][index]:
                        continue
                    observed = nearest_temperature(history, [entity_id], target, tolerance)
                    if observed is not None:
                        values["observed"][index] = observed
                        features = self.hybrid.room_features(
                            snapshot["outdoor_now"],
                            snapshot["outdoor"][index],
                            values["slope"],
                            snapshot["radiation"][index],
                            horizon,
                        )
                        self.hybrid.update_room(
                            entity_id,
                            horizon,
                            features,
                            values["base"],
                            values["hybrid"][index],
                            observed,
                        )
                        values["error"][index] = round(values["hybrid"][index] - observed, 3)
                    if observed is not None or now - target > timedelta(hours=2):
                        values["validated"][index] = True
                        changed = True

            if self._snapshot_fully_validated(snapshot):
                self._compact_snapshot(snapshot)
                changed = True

        self.storage.data["hybrid_state"] = self.hybrid.to_dict()
        return changed

    @staticmethod
    def _snapshot_fully_validated(snapshot: dict[str, Any]) -> bool:
        return all(
            all(values["validated"])
            for group in ("external", "rooms")
            for values in snapshot.get(group, {}).values()
        )

    @staticmethod
    def _compact_snapshot(snapshot: dict[str, Any]) -> None:
        """Discard learning-only duplication after a snapshot is fully verified."""
        models = snapshot["models"]
        for values in snapshot.get("external", {}).values():
            selected = values["selected"]
            values["raw"] = [models[model]["raw"][index] for index, model in enumerate(selected)]
            values["raw_error"] = [
                values["raw_error"][model][index] for index, model in enumerate(selected)
            ]
            values["hybrid_error"] = [
                values["hybrid_error"][model][index] for index, model in enumerate(selected)
            ]
            for key in ("slope", "hybrid_by_model", "validated"):
                values.pop(key, None)
        for values in snapshot.get("rooms", {}).values():
            values.pop("validated", None)
        snapshot.pop("models", None)
        snapshot["compact"] = True
