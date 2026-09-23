"""Coordinator for weather collection, verification and hybrid forecasts."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
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
from .open_meteo import OpenMeteoClient, OpenMeteoError
from .storage import ThermalStore

_LOGGER = logging.getLogger(__name__)


class LocalThermalForecastCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinate all data and learning for one home."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: OpenMeteoClient,
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

        all_sensors = list(dict.fromkeys(self.external_sensors + self.room_sensors))
        recent_history = await async_temperature_history(
            self.hass, all_sensors, now - timedelta(hours=3), now
        )
        if await self._async_validate_pending(now, recent_history):
            self.storage.async_delay_save()

        try:
            bundle = await self.client.async_fetch(latitude, longitude)
        except OpenMeteoError as err:
            raise UpdateFailed(str(err)) from err

        primary = next(iter(bundle.forecasts.values()))
        observed_outdoor = current_temperature(self.hass, self.external_sensors)
        if observed_outdoor is None:
            observed_outdoor = primary.points[0].temperature
        outdoor_slope = temperature_slope(recent_history, self.external_sensors)

        room_temperatures: dict[str, float] = {}
        room_slopes: dict[str, float] = {}
        for entity_id in self.room_sensors:
            if (temperature := current_temperature(self.hass, [entity_id])) is not None:
                room_temperatures[entity_id] = temperature
                room_slopes[entity_id] = temperature_slope(recent_history, [entity_id])

        candidate_predictions: dict[str, list[float]] = {}
        for model, forecast in bundle.forecasts.items():
            raw_now = forecast.points[0].temperature
            predictions: list[float] = []
            for horizon in range(1, OUTDOOR_HOURS + 1):
                raw_target = forecast.points[horizon].temperature
                features = self.hybrid.outdoor_features(raw_now, raw_target, outdoor_slope, horizon)
                predictions.append(
                    self.hybrid.predict_outdoor(model, horizon, observed_outdoor, features)
                )
            candidate_predictions[model] = predictions

        selected_models: dict[int, str] = {}
        outdoor_points: list[HybridForecastPoint] = []
        radiation: list[float | None] = []
        for horizon in range(1, OUTDOOR_HOURS + 1):
            selected = self.hybrid.select_model(horizon, set(bundle.forecasts))
            selected_models[horizon] = selected
            source = bundle.forecasts[selected].points[horizon]
            radiation.append(source.shortwave_radiation)
            outdoor_points.append(
                HybridForecastPoint(
                    valid_at=source.valid_at,
                    temperature=round(candidate_predictions[selected][horizon - 1], 2),
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

        room_forecasts: dict[str, tuple[RoomForecastPoint, ...]] = {}
        room_prediction_arrays: dict[str, list[float]] = {}
        for entity_id, room_temperature in room_temperatures.items():
            points: list[RoomForecastPoint] = []
            values: list[float] = []
            for horizon in range(1, ROOM_HOURS + 1):
                features = self.hybrid.room_features(
                    observed_outdoor,
                    outdoor_points[horizon - 1].temperature,
                    room_slopes[entity_id],
                    radiation[horizon - 1],
                    horizon,
                )
                predicted = self.hybrid.predict_room(entity_id, horizon, room_temperature, features)
                values.append(predicted)
                points.append(
                    RoomForecastPoint(outdoor_points[horizon - 1].valid_at, round(predicted, 2))
                )
            room_prediction_arrays[entity_id] = values
            room_forecasts[entity_id] = tuple(points)

        data = CoordinatorData(
            issued_at=bundle.retrieved_at,
            outdoor_temperature=round(observed_outdoor, 2),
            outdoor_forecast=tuple(outdoor_points),
            room_temperatures=room_temperatures,
            room_forecasts=room_forecasts,
            selected_models=selected_models,
        )
        self._append_snapshot(
            data,
            bundle.forecasts,
            candidate_predictions,
            outdoor_slope,
            room_slopes,
            room_prediction_arrays,
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
        candidate_predictions: dict[str, list[float]],
        outdoor_slope: float,
        room_slopes: dict[str, float],
        room_predictions: dict[str, list[float]],
        radiation: list[float | None],
    ) -> None:
        """Append a compact, auditable issued forecast."""
        self.storage.snapshots.append(
            {
                "purpose": "home_outdoor_temperature",
                "gateway": "open_meteo",
                "issued_at": data.issued_at.isoformat(),
                "valid_at": [point.valid_at.isoformat() for point in data.outdoor_forecast],
                "local_temperature": data.outdoor_temperature,
                "local_slope": outdoor_slope,
                "models": {
                    model: {
                        "provider_run_at": None,
                        "raw_now": forecast.points[0].temperature,
                        "raw": [
                            forecast.points[h].temperature for h in range(1, OUTDOOR_HOURS + 1)
                        ],
                        "hybrid": candidate_predictions[model],
                        "raw_error": [None] * OUTDOOR_HOURS,
                        "hybrid_error": [None] * OUTDOOR_HOURS,
                    }
                    for model, forecast in forecasts.items()
                },
                "selected": [data.selected_models[h] for h in range(1, OUTDOOR_HOURS + 1)],
                "outdoor": [point.temperature for point in data.outdoor_forecast],
                "observed": [None] * OUTDOOR_HOURS,
                "radiation": radiation,
                "rooms": {
                    entity_id: {
                        "base": data.room_temperatures[entity_id],
                        "slope": room_slopes[entity_id],
                        "hybrid": predictions,
                        "observed": [None] * ROOM_HOURS,
                        "error": [None] * ROOM_HOURS,
                        "validated": [False] * ROOM_HOURS,
                    }
                    for entity_id, predictions in room_predictions.items()
                },
                "validated": [False] * OUTDOOR_HOURS,
            }
        )

    async def _async_validate_pending(
        self,
        now: datetime,
        recent_history: dict[str, list[tuple[datetime, float]]],
    ) -> bool:
        """Match issued forecasts to Recorder observations and learn."""
        pending_targets: list[datetime] = []
        for snapshot in self.storage.snapshots:
            for index, value in enumerate(snapshot["valid_at"]):
                target = datetime.fromisoformat(value)
                outdoor_pending = not snapshot["validated"][index]
                room_pending = index < ROOM_HOURS and any(
                    not room.get("validated", [False] * ROOM_HOURS)[index]
                    for room in snapshot.get("rooms", {}).values()
                )
                if (outdoor_pending or room_pending) and target <= now - timedelta(minutes=5):
                    pending_targets.append(target)
        if not pending_targets:
            return False

        start = min(pending_targets) - HISTORY_TOLERANCE
        history = recent_history
        if start < now - timedelta(hours=3):
            history = await async_temperature_history(
                self.hass,
                list(dict.fromkeys(self.external_sensors + self.room_sensors)),
                start,
                now,
            )
        tolerance = HISTORY_TOLERANCE.total_seconds()
        changed = False

        for snapshot in self.storage.snapshots:
            for index, value in enumerate(snapshot["valid_at"]):
                target = datetime.fromisoformat(value)
                if target > now - timedelta(minutes=5):
                    continue
                horizon = index + 1
                if not snapshot["validated"][index]:
                    observed = nearest_temperature(
                        history, self.external_sensors, target, tolerance
                    )
                    if observed is not None:
                        snapshot.setdefault("observed", [None] * OUTDOOR_HOURS)[index] = observed
                        for model, values in snapshot["models"].items():
                            features = self.hybrid.outdoor_features(
                                values["raw_now"],
                                values["raw"][index],
                                snapshot["local_slope"],
                                horizon,
                            )
                            self.hybrid.update_outdoor(
                                model,
                                horizon,
                                features,
                                snapshot["local_temperature"],
                                values["raw"][index],
                                values["hybrid"][index],
                                observed,
                            )
                            values.setdefault("raw_error", [None] * OUTDOOR_HOURS)[index] = round(
                                values["raw"][index] - observed, 3
                            )
                            values.setdefault("hybrid_error", [None] * OUTDOOR_HOURS)[index] = (
                                round(values["hybrid"][index] - observed, 3)
                            )
                    if observed is not None or now - target > timedelta(hours=2):
                        snapshot["validated"][index] = True
                        changed = True
                if index < ROOM_HOURS:
                    for entity_id, values in snapshot.get("rooms", {}).items():
                        room_validated = values.setdefault("validated", [False] * ROOM_HOURS)
                        if room_validated[index]:
                            continue
                        room_observed = nearest_temperature(history, [entity_id], target, tolerance)
                        if room_observed is not None:
                            values.setdefault("observed", [None] * ROOM_HOURS)[index] = (
                                room_observed
                            )
                            features = self.hybrid.room_features(
                                snapshot["local_temperature"],
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
                                room_observed,
                            )
                            values.setdefault("error", [None] * ROOM_HOURS)[index] = round(
                                values["hybrid"][index] - room_observed, 3
                            )
                        if room_observed is not None or now - target > timedelta(hours=2):
                            room_validated[index] = True
                            changed = True

        self.storage.data["hybrid_state"] = self.hybrid.to_dict()
        return changed

    def room_forecast_response(self, entity_id: str) -> dict[str, Any]:
        """Return a response-safe full room series."""
        if self.data is None or entity_id not in self.data.room_forecasts:
            return {"room_entity_id": entity_id, "forecast": []}
        return {
            "room_entity_id": entity_id,
            "issued_at": self.data.issued_at.isoformat(),
            "forecast": [point.as_dict() for point in self.data.room_forecasts[entity_id]],
        }
