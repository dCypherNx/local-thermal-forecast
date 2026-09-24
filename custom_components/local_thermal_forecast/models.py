"""Provider-neutral forecast structures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class WeatherPoint:
    """A normalized hourly weather point."""

    valid_at: datetime
    temperature: float
    apparent_temperature: float | None = None
    humidity: float | None = None
    precipitation: float | None = None
    precipitation_probability: int | None = None
    weather_code: int | None = None
    cloud_cover: int | None = None
    wind_speed: float | None = None
    wind_gust: float | None = None
    shortwave_radiation: float | None = None


@dataclass(frozen=True, slots=True)
class ModelForecast:
    """Normalized forecast from one numerical model."""

    model: str
    points: tuple[WeatherPoint, ...]


@dataclass(frozen=True, slots=True)
class ForecastBundle:
    """Forecasts fetched together from a provider gateway."""

    retrieved_at: datetime
    requested_latitude: float
    requested_longitude: float
    grid_latitude: float
    grid_longitude: float
    elevation: float | None
    gateway: str
    forecasts: dict[str, ModelForecast]


@dataclass(frozen=True, slots=True)
class HybridForecastPoint:
    """A published hybrid forecast point."""

    valid_at: datetime
    temperature: float
    raw_temperature: float
    model: str
    apparent_temperature: float | None = None
    humidity: float | None = None
    precipitation: float | None = None
    precipitation_probability: int | None = None
    weather_code: int | None = None
    cloud_cover: int | None = None
    wind_speed: float | None = None
    wind_gust: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary."""
        result = asdict(self)
        result["valid_at"] = self.valid_at.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HybridForecastPoint:
        """Restore a point from storage."""
        return cls(
            **{
                **data,
                "valid_at": datetime.fromisoformat(data["valid_at"]),
            }
        )


@dataclass(frozen=True, slots=True)
class RoomForecastPoint:
    """A forecast point for an indoor room."""

    valid_at: datetime
    temperature: float

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary."""
        return {"valid_at": self.valid_at.isoformat(), "temperature": self.temperature}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoomForecastPoint:
        """Restore a room point from storage."""
        return cls(datetime.fromisoformat(data["valid_at"]), data["temperature"])


@dataclass(frozen=True, slots=True)
class CoordinatorData:
    """Data shared by Home Assistant entities."""

    issued_at: datetime
    external_temperatures: dict[str, float]
    external_forecasts: dict[str, tuple[HybridForecastPoint, ...]]
    room_temperatures: dict[str, float]
    room_forecasts: dict[str, tuple[RoomForecastPoint, ...]]
    selected_models: dict[str, dict[int, str]]

    def as_dict(self) -> dict[str, Any]:
        """Return data suitable for Store."""
        return {
            "issued_at": self.issued_at.isoformat(),
            "external_temperatures": self.external_temperatures,
            "external_forecasts": {
                entity_id: [point.as_dict() for point in points]
                for entity_id, points in self.external_forecasts.items()
            },
            "room_temperatures": self.room_temperatures,
            "room_forecasts": {
                entity_id: [point.as_dict() for point in points]
                for entity_id, points in self.room_forecasts.items()
            },
            "selected_models": {
                entity_id: {str(horizon): model for horizon, model in horizons.items()}
                for entity_id, horizons in self.selected_models.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoordinatorData:
        """Restore coordinator data from Store."""
        return cls(
            issued_at=datetime.fromisoformat(data["issued_at"]),
            external_temperatures=data.get("external_temperatures", {}),
            external_forecasts={
                entity_id: tuple(HybridForecastPoint.from_dict(point) for point in points)
                for entity_id, points in data.get("external_forecasts", {}).items()
            },
            room_temperatures=data.get("room_temperatures", {}),
            room_forecasts={
                entity_id: tuple(RoomForecastPoint.from_dict(point) for point in points)
                for entity_id, points in data.get("room_forecasts", {}).items()
            },
            selected_models={
                entity_id: {int(horizon): model for horizon, model in horizons.items()}
                for entity_id, horizons in data.get("selected_models", {}).items()
            },
        )
