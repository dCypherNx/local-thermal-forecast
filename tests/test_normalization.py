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
open_meteo = load_module("open_meteo")


class NormalizationTests(unittest.TestCase):
    def test_multi_model_response_is_split_and_typed(self) -> None:
        start = datetime(2026, 9, 23, tzinfo=UTC)
        times = [(start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M") for index in range(25)]
        hourly: dict[str, list] = {"time": times}
        for model_index, model in enumerate(const.MODEL_IDS):
            hourly[f"temperature_2m_{model}"] = [
                15.0 + model_index + index / 10 for index in range(25)
            ]
            hourly[f"weather_code_{model}"] = [3] * 25
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
        self.assertEqual(len(bundle.forecasts["ecmwf_ifs"].points), 25)
        self.assertEqual(bundle.forecasts["icon_global"].points[0].weather_code, 3)
        self.assertEqual(bundle.gateway, "open_meteo")


if __name__ == "__main__":
    unittest.main()
