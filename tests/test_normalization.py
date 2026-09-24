"""Tests for multi-model Open-Meteo normalization."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from types import ModuleType

aiohttp = ModuleType("aiohttp")
aiohttp.ClientError = type("ClientError", (Exception,), {})
aiohttp.ClientSession = object
sys.modules.setdefault("aiohttp", aiohttp)

from _loader import load_module  # noqa: E402

const = load_module("const")
models = load_module("models")
open_meteo = load_module("open_meteo")


class NormalizationTests(unittest.TestCase):
    def test_multi_model_response_is_split_and_typed(self) -> None:
        start = datetime(2026, 9, 23, tzinfo=UTC)
        times = [(start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M") for index in range(26)]
        hourly: dict[str, list] = {"time": times}
        for model_index, model in enumerate(const.MODEL_IDS):
            hourly[f"temperature_2m_{model}"] = [
                15.0 + model_index + index / 10 for index in range(26)
            ]
            hourly[f"weather_code_{model}"] = [3] * 26
        bundle = open_meteo.normalize_response(
            {
                "latitude": -23.5,
                "longitude": -46.6,
                "elevation": 737,
                "hourly": hourly,
            },
            requested_latitude=-23.55,
            requested_longitude=-46.63,
            retrieved_at=start,
        )
        self.assertEqual(set(bundle.forecasts), set(const.MODEL_IDS))
        self.assertEqual(len(bundle.forecasts["ecmwf_ifs"].points), 26)
        self.assertEqual(bundle.forecasts["icon_global"].points[0].weather_code, 3)
        self.assertEqual(bundle.gateway, "open_meteo")

    def test_unsuffixed_ecmwf_response_is_normalized_without_optional_fields(self) -> None:
        start = datetime(2026, 9, 24, tzinfo=UTC)
        times = [(start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M") for index in range(50)]
        bundle = open_meteo.normalize_response(
            {
                "latitude": -23.5,
                "longitude": -46.6,
                "elevation": 737,
                "hourly": {
                    "time": times,
                    "temperature_2m": [15.0 + index / 10 for index in range(50)],
                },
            },
            requested_latitude=-23.55,
            requested_longitude=-46.63,
            retrieved_at=start + timedelta(hours=3, minutes=20),
        )
        self.assertEqual(set(bundle.forecasts), {"ecmwf_ifs"})
        self.assertEqual(len(bundle.forecasts["ecmwf_ifs"].points), 50)
        self.assertIsNone(bundle.forecasts["ecmwf_ifs"].points[0].humidity)

    def test_resampling_produces_exact_hourly_leads(self) -> None:
        retrieved_at = datetime(2026, 9, 23, 10, 17, 32, tzinfo=UTC)
        source = models.ModelForecast(
            "ecmwf_ifs",
            tuple(
                models.WeatherPoint(
                    datetime(2026, 9, 23, 10, tzinfo=UTC) + timedelta(hours=index),
                    20.0 + index,
                )
                for index in range(26)
            ),
        )
        result = open_meteo.resample_forecast(source, retrieved_at)
        self.assertEqual(
            result.points[1].valid_at,
            datetime(2026, 9, 23, 11, 17, 32, tzinfo=UTC),
        )
        self.assertEqual(
            result.points[24].valid_at,
            datetime(2026, 9, 24, 10, 17, 32, tzinfo=UTC),
        )
        self.assertAlmostEqual(result.points[0].temperature, 20.292222, places=5)


if __name__ == "__main__":
    unittest.main()
