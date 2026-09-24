"""Tests for persisted per-sensor forecast data."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from _loader import load_module

models = load_module("models")


class CoordinatorDataTests(unittest.TestCase):
    def test_round_trip_preserves_each_sensor_forecast(self) -> None:
        issued_at = datetime(2026, 9, 23, tzinfo=UTC)
        outdoor = models.HybridForecastPoint(issued_at, 22.5, 22.0, "ecmwf_ifs", weather_code=2)
        room = models.RoomForecastPoint(issued_at, 24.0)
        original = models.CoordinatorData(
            issued_at=issued_at,
            external_temperatures={"sensor.front": 21.0, "sensor.back": 20.5},
            external_current={"sensor.front": outdoor, "sensor.back": outdoor},
            external_forecasts={
                "sensor.front": (outdoor,),
                "sensor.back": (outdoor,),
            },
            room_temperatures={"sensor.room": 23.0},
            room_forecasts={"sensor.room": (room,)},
            selected_models={
                "sensor.front": {1: "ecmwf_ifs"},
                "sensor.back": {1: "icon_global"},
            },
            source_available={
                "sensor.front": True,
                "sensor.back": True,
                "sensor.room": True,
            },
        )

        restored = models.CoordinatorData.from_dict(original.as_dict())

        self.assertEqual(set(restored.external_forecasts), {"sensor.front", "sensor.back"})
        self.assertEqual(restored.external_forecasts["sensor.front"][0].temperature, 22.5)
        self.assertEqual(restored.room_forecasts["sensor.room"][0].temperature, 24.0)
        self.assertEqual(restored.selected_models["sensor.back"][1], "icon_global")


if __name__ == "__main__":
    unittest.main()
