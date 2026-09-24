"""Explainable online hybrid thermal models."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .const import PRIMARY_MODEL

MIN_SELECTION_SAMPLES = 12
SWITCH_MARGIN = 0.05
EWMA_ALPHA = 0.12
FORGETTING_FACTOR = 0.995


@dataclass(slots=True)
class RunningMetrics:
    """Online error metrics with both cumulative and recent values."""

    count: int = 0
    error_sum: float = 0.0
    absolute_error_sum: float = 0.0
    squared_error_sum: float = 0.0
    ewma_mae: float | None = None

    def update(self, error: float) -> None:
        """Add an error sample."""
        self.count += 1
        self.error_sum += error
        self.absolute_error_sum += abs(error)
        self.squared_error_sum += error * error
        if self.ewma_mae is None:
            self.ewma_mae = abs(error)
        else:
            self.ewma_mae += EWMA_ALPHA * (abs(error) - self.ewma_mae)

    @property
    def bias(self) -> float | None:
        return None if not self.count else self.error_sum / self.count

    @property
    def mae(self) -> float | None:
        return None if not self.count else self.absolute_error_sum / self.count

    @property
    def rmse(self) -> float | None:
        return None if not self.count else math.sqrt(self.squared_error_sum / self.count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "error_sum": self.error_sum,
            "absolute_error_sum": self.absolute_error_sum,
            "squared_error_sum": self.squared_error_sum,
            "ewma_mae": self.ewma_mae,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunningMetrics:
        return cls(**data)


@dataclass(slots=True)
class RecursiveLeastSquares:
    """Small pure-Python recursive linear regression."""

    weights: list[float]
    covariance: list[list[float]]
    count: int = 0

    @classmethod
    def create(cls, default_weights: list[float]) -> RecursiveLeastSquares:
        size = len(default_weights)
        covariance = [[100.0 if row == col else 0.0 for col in range(size)] for row in range(size)]
        return cls(list(default_weights), covariance)

    def predict(self, features: list[float]) -> float:
        return sum(weight * value for weight, value in zip(self.weights, features, strict=True))

    def update(self, features: list[float], target: float) -> None:
        """Perform one bounded RLS update."""
        prediction = self.predict(features)
        residual = max(-8.0, min(8.0, target - prediction))
        px = [sum(row[j] * features[j] for j in range(len(features))) for row in self.covariance]
        denominator = FORGETTING_FACTOR + sum(
            features[index] * px[index] for index in range(len(features))
        )
        gain = [value / denominator for value in px]
        self.weights = [
            max(-5.0, min(5.0, weight + gain[index] * residual))
            for index, weight in enumerate(self.weights)
        ]
        x_covariance = [
            sum(features[k] * self.covariance[k][column] for k in range(len(features)))
            for column in range(len(features))
        ]
        self.covariance = [
            [
                (self.covariance[row][column] - gain[row] * x_covariance[column])
                / FORGETTING_FACTOR
                for column in range(len(features))
            ]
            for row in range(len(features))
        ]
        self.count += 1

    def to_dict(self) -> dict[str, Any]:
        return {"weights": self.weights, "covariance": self.covariance, "count": self.count}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecursiveLeastSquares:
        return cls(data["weights"], data["covariance"], data.get("count", 0))


@dataclass(slots=True)
class HorizonModel:
    """Learned model and verification metrics for one horizon."""

    regression: RecursiveLeastSquares
    raw_metrics: RunningMetrics = field(default_factory=RunningMetrics)
    hybrid_metrics: RunningMetrics = field(default_factory=RunningMetrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "regression": self.regression.to_dict(),
            "raw_metrics": self.raw_metrics.to_dict(),
            "hybrid_metrics": self.hybrid_metrics.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HorizonModel:
        return cls(
            RecursiveLeastSquares.from_dict(data["regression"]),
            RunningMetrics.from_dict(data["raw_metrics"]),
            RunningMetrics.from_dict(data["hybrid_metrics"]),
        )


class HybridSystem:
    """Outdoor model comparison and independent room models."""

    def __init__(self) -> None:
        self.outdoor: dict[str, dict[str, dict[int, HorizonModel]]] = {}
        self.rooms: dict[str, dict[int, HorizonModel]] = {}
        self.champions: dict[str, dict[int, str]] = {}

    def _outdoor_model(self, entity_id: str, model: str, horizon: int) -> HorizonModel:
        return (
            self.outdoor.setdefault(entity_id, {})
            .setdefault(model, {})
            .setdefault(
                horizon,
                HorizonModel(RecursiveLeastSquares.create([0.0, 1.0, 1.0])),
            )
        )

    def _room_model(self, entity_id: str, horizon: int) -> HorizonModel:
        return self.rooms.setdefault(entity_id, {}).setdefault(
            horizon,
            HorizonModel(RecursiveLeastSquares.create([0.0, 0.20, 1.0, 0.0])),
        )

    @staticmethod
    def outdoor_features(
        raw_now: float, raw_target: float, local_slope: float, horizon: int
    ) -> list[float]:
        return [1.0, raw_target - raw_now, local_slope * min(horizon, 3)]

    @staticmethod
    def room_features(
        outdoor_now: float,
        outdoor_target: float,
        room_slope: float,
        radiation: float | None,
        horizon: int,
    ) -> list[float]:
        return [
            1.0,
            outdoor_target - outdoor_now,
            room_slope * min(horizon, 3),
            (radiation or 0.0) / 500.0,
        ]

    def predict_outdoor(
        self,
        entity_id: str,
        model: str,
        horizon: int,
        local_temperature: float,
        features: list[float],
    ) -> float:
        state = self.outdoor.get(entity_id, {}).get(model, {}).get(horizon)
        correction = (
            state.regression.predict(features)
            if state is not None
            else RecursiveLeastSquares.create([0.0, 1.0, 1.0]).predict(features)
        )
        return local_temperature + correction

    def predict_room(
        self,
        entity_id: str,
        horizon: int,
        room_temperature: float,
        features: list[float],
    ) -> float:
        state = self.rooms.get(entity_id, {}).get(horizon)
        correction = (
            state.regression.predict(features)
            if state is not None
            else RecursiveLeastSquares.create([0.0, 0.20, 1.0, 0.0]).predict(features)
        )
        return room_temperature + correction

    def update_outdoor(
        self,
        entity_id: str,
        model: str,
        horizon: int,
        features: list[float],
        local_temperature: float,
        raw_temperature: float,
        predicted_temperature: float,
        observed_temperature: float,
    ) -> None:
        state = self._outdoor_model(entity_id, model, horizon)
        state.raw_metrics.update(raw_temperature - observed_temperature)
        state.hybrid_metrics.update(predicted_temperature - observed_temperature)
        state.regression.update(features, observed_temperature - local_temperature)

    def update_room(
        self,
        entity_id: str,
        horizon: int,
        features: list[float],
        room_temperature: float,
        predicted_temperature: float,
        observed_temperature: float,
    ) -> None:
        state = self._room_model(entity_id, horizon)
        state.hybrid_metrics.update(predicted_temperature - observed_temperature)
        state.regression.update(features, observed_temperature - room_temperature)

    def select_model(self, entity_id: str, horizon: int, available: set[str]) -> str:
        """Select a proven model with hysteresis; otherwise use ECMWF."""
        if not available:
            raise ValueError("At least one weather model is required")
        fallback = PRIMARY_MODEL if PRIMARY_MODEL in available else sorted(available)[0]
        eligible: dict[str, float] = {}
        for model in available:
            state = self.outdoor.get(entity_id, {}).get(model, {}).get(horizon)
            if (
                state is not None
                and state.hybrid_metrics.count >= MIN_SELECTION_SAMPLES
                and state.hybrid_metrics.ewma_mae is not None
            ):
                eligible[model] = state.hybrid_metrics.ewma_mae
        if not eligible:
            self.champions.setdefault(entity_id, {})[horizon] = fallback
            return fallback

        best = min(eligible, key=eligible.get)  # type: ignore[arg-type]
        current = self.champions.get(entity_id, {}).get(horizon, fallback)
        if current not in eligible or eligible[best] < eligible[current] * (1.0 - SWITCH_MARGIN):
            current = best
        self.champions.setdefault(entity_id, {})[horizon] = current
        return current

    def metrics(self, entity_id: str, horizon: int) -> dict[str, Any]:
        """Return metrics for the selected model at a horizon."""
        model = self.champions.get(entity_id, {}).get(horizon, PRIMARY_MODEL)
        state = self.outdoor.get(entity_id, {}).get(model, {}).get(horizon)
        if state is None:
            return {"model": model, "count": 0, "bias": None, "mae": None, "rmse": None}
        metrics = state.hybrid_metrics
        return {
            "model": model,
            "count": metrics.count,
            "bias": metrics.bias,
            "mae": metrics.mae,
            "rmse": metrics.rmse,
            "raw_mae": state.raw_metrics.mae,
        }

    def room_metrics(self, entity_id: str, horizon: int) -> dict[str, Any]:
        """Return validation metrics for one indoor sensor and horizon."""
        state = self.rooms.get(entity_id, {}).get(horizon)
        if state is None:
            return {"count": 0, "bias": None, "mae": None, "rmse": None}
        metrics = state.hybrid_metrics
        return {
            "count": metrics.count,
            "bias": metrics.bias,
            "mae": metrics.mae,
            "rmse": metrics.rmse,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "outdoor": {
                entity_id: {
                    model: {
                        str(horizon): state.to_dict()
                        for horizon, state in horizons.items()
                        if state.regression.count > 0
                    }
                    for model, horizons in models.items()
                    if any(state.regression.count > 0 for state in horizons.values())
                }
                for entity_id, models in self.outdoor.items()
                if any(
                    state.regression.count > 0
                    for horizons in models.values()
                    for state in horizons.values()
                )
            },
            "rooms": {
                entity_id: {
                    str(horizon): state.to_dict()
                    for horizon, state in horizons.items()
                    if state.regression.count > 0
                }
                for entity_id, horizons in self.rooms.items()
                if any(state.regression.count > 0 for state in horizons.values())
            },
            "champions": {
                entity_id: {str(horizon): model for horizon, model in horizons.items()}
                for entity_id, horizons in self.champions.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HybridSystem:
        system = cls()
        if not data:
            return system
        system.outdoor = {
            entity_id: {
                model: {
                    int(horizon): HorizonModel.from_dict(state)
                    for horizon, state in horizons.items()
                }
                for model, horizons in models.items()
            }
            for entity_id, models in data.get("outdoor", {}).items()
        }
        system.rooms = {
            entity_id: {
                int(horizon): HorizonModel.from_dict(state) for horizon, state in horizons.items()
            }
            for entity_id, horizons in data.get("rooms", {}).items()
        }
        system.champions = {
            entity_id: {int(horizon): model for horizon, model in horizons.items()}
            for entity_id, horizons in data.get("champions", {}).items()
        }
        return system
