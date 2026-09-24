"""Read temperature observations without duplicating Recorder history."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from functools import partial
from statistics import median
from typing import Any

from homeassistant.components.recorder.history import get_significant_states
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.recorder import get_instance
from homeassistant.util.unit_conversion import TemperatureConverter


def _temperature_from_state(state: State | None) -> float | None:
    if state is None or state.state in {"unknown", "unavailable", "none", ""}:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, UnitOfTemperature.CELSIUS)
    try:
        return float(TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS))
    except (HomeAssistantError, TypeError, ValueError):
        return None


def current_temperature(hass: HomeAssistant, entity_ids: Iterable[str]) -> float | None:
    """Return the median of currently valid sensors."""
    values = [
        value
        for entity_id in entity_ids
        if (value := _temperature_from_state(hass.states.get(entity_id))) is not None
    ]
    return median(values) if values else None


async def async_temperature_history(
    hass: HomeAssistant,
    entity_ids: list[str],
    start: datetime,
    end: datetime,
) -> dict[str, list[tuple[datetime, float]]]:
    """Read temperature history through the Recorder API."""
    if not entity_ids:
        return {}
    query = partial(
        get_significant_states,
        hass,
        start,
        end,
        entity_ids,
        include_start_time_state=True,
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=False,
    )
    try:
        result: dict[str, list[State | dict[str, Any]]] = await get_instance(
            hass
        ).async_add_executor_job(query)
    except (KeyError, RuntimeError, ValueError):
        return {}

    converted: dict[str, list[tuple[datetime, float]]] = {}
    for entity_id, states in result.items():
        points: list[tuple[datetime, float]] = []
        for item in states:
            if not isinstance(item, State):
                continue
            value = _temperature_from_state(item)
            if value is not None:
                points.append((item.last_updated, value))
        converted[entity_id] = points
    return converted


def nearest_temperature(
    history: dict[str, list[tuple[datetime, float]]],
    entity_ids: Iterable[str],
    target: datetime,
    tolerance_seconds: float,
) -> float | None:
    """Return the last known observation at a target instant.

    A state just after the target is only used when Recorder has no preceding
    state. This avoids validating a forecast with information from the future.
    """
    values: list[float] = []
    for entity_id in entity_ids:
        points = history.get(entity_id, [])
        if not points:
            continue
        preceding = [point for point in points if point[0] <= target]
        selected_time, selected_value = (
            max(preceding, key=lambda point: point[0])
            if preceding
            else min(points, key=lambda point: point[0])
        )
        age = (target - selected_time).total_seconds()
        if -300 <= age <= tolerance_seconds:
            values.append(selected_value)
    return median(values) if values else None


def temperature_slope(
    history: dict[str, list[tuple[datetime, float]]], entity_ids: Iterable[str]
) -> float:
    """Estimate a robust Celsius-per-hour trend from recent history."""
    slopes: list[float] = []
    for entity_id in entity_ids:
        points = history.get(entity_id, [])
        if len(points) < 2:
            continue
        first_time, first_value = points[0]
        last_time, last_value = points[-1]
        hours = (last_time - first_time).total_seconds() / 3600
        if hours >= 0.25:
            slopes.append(max(-5.0, min(5.0, (last_value - first_value) / hours)))
    return median(slopes) if slopes else 0.0
