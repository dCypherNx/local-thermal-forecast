"""Async Open-Meteo client and response normalization."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import MODEL_IDS, OUTDOOR_HOURS
from .models import ForecastBundle, ModelForecast, WeatherPoint

_LOGGER = logging.getLogger(__name__)

API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "precipitation_probability",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
    "shortwave_radiation",
)


class OpenMeteoError(Exception):
    """Raised when Open-Meteo cannot supply a usable forecast."""


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _value(hourly: dict[str, list[Any]], field: str, model: str, index: int) -> Any:
    for key in (f"{field}_{model}", field):
        values = hourly.get(key)
        if values is not None and index < len(values):
            return values[index]
    return None


def normalize_response(
    payload: dict[str, Any],
    *,
    requested_latitude: float,
    requested_longitude: float,
    retrieved_at: datetime,
) -> ForecastBundle:
    """Normalize a multi-model Open-Meteo response."""
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or not hourly.get("time"):
        raise OpenMeteoError("Open-Meteo response contains no hourly forecast")

    times = [_parse_time(value) for value in hourly["time"]]
    forecasts: dict[str, ModelForecast] = {}
    for model in MODEL_IDS:
        points: list[WeatherPoint] = []
        for index, valid_at in enumerate(times):
            temperature = _value(hourly, "temperature_2m", model, index)
            if temperature is None:
                continue
            points.append(
                WeatherPoint(
                    valid_at=valid_at,
                    temperature=float(temperature),
                    apparent_temperature=_optional_float(
                        _value(hourly, "apparent_temperature", model, index)
                    ),
                    humidity=_optional_float(_value(hourly, "relative_humidity_2m", model, index)),
                    precipitation=_optional_float(_value(hourly, "precipitation", model, index)),
                    precipitation_probability=_optional_int(
                        _value(hourly, "precipitation_probability", model, index)
                    ),
                    weather_code=_optional_int(_value(hourly, "weather_code", model, index)),
                    cloud_cover=_optional_int(_value(hourly, "cloud_cover", model, index)),
                    wind_speed=_optional_float(_value(hourly, "wind_speed_10m", model, index)),
                    wind_gust=_optional_float(_value(hourly, "wind_gusts_10m", model, index)),
                    shortwave_radiation=_optional_float(
                        _value(hourly, "shortwave_radiation", model, index)
                    ),
                )
            )
        if len(points) >= OUTDOOR_HOURS + 1:
            forecasts[model] = ModelForecast(model, tuple(points[: OUTDOOR_HOURS + 1]))

    if not forecasts:
        raise OpenMeteoError("No configured model returned a complete 24-hour forecast")

    return ForecastBundle(
        retrieved_at=retrieved_at,
        requested_latitude=requested_latitude,
        requested_longitude=requested_longitude,
        grid_latitude=float(payload["latitude"]),
        grid_longitude=float(payload["longitude"]),
        elevation=_optional_float(payload.get("elevation")),
        gateway="open_meteo",
        forecasts=forecasts,
    )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


class OpenMeteoClient:
    """Fetch multi-model weather data from Open-Meteo."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def async_fetch(self, latitude: float, longitude: float) -> ForecastBundle:
        """Fetch and normalize the candidate model forecasts."""
        params = {
            "latitude": str(latitude),
            "longitude": str(longitude),
            "models": ",".join(MODEL_IDS),
            "hourly": ",".join(HOURLY_FIELDS),
            "forecast_hours": str(OUTDOOR_HOURS + 1),
            "timezone": "UTC",
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
        }
        retrieved_at = datetime.now(UTC)
        try:
            async with self._session.get(API_URL, params=params, timeout=30) as response:
                if response.status != 200:
                    detail = (await response.text())[:300]
                    raise OpenMeteoError(f"Open-Meteo returned HTTP {response.status}: {detail}")
                payload = await response.json()
        except (TimeoutError, ClientError) as err:
            raise OpenMeteoError(f"Open-Meteo request failed: {err}") from err

        return normalize_response(
            payload,
            requested_latitude=latitude,
            requested_longitude=longitude,
            retrieved_at=retrieved_at,
        )
