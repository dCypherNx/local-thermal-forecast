"""Tests for the explainable hybrid model."""

from __future__ import annotations

import unittest

from _loader import load_module

hybrid = load_module("hybrid")


class HybridSystemTests(unittest.TestCase):
    def test_cold_start_anchors_weather_change_to_local_sensor(self) -> None:
        system = hybrid.HybridSystem()
        features = system.outdoor_features(15.0, 18.0, 0.2, 2)
        self.assertAlmostEqual(system.predict_outdoor("ecmwf_ifs", 2, 20.0, features), 23.4)

    def test_better_model_becomes_champion_after_minimum_sample(self) -> None:
        system = hybrid.HybridSystem()
        features = system.outdoor_features(20.0, 21.0, 0.0, 1)
        for _ in range(hybrid.MIN_SELECTION_SAMPLES):
            system.update_outdoor("ecmwf_ifs", 1, features, 20.0, 24.0, 21.0, 21.0)
            system.update_outdoor("icon_global", 1, features, 20.0, 21.1, 21.0, 21.0)
        selected = system.select_model(1, {"ecmwf_ifs", "icon_global"})
        self.assertEqual(selected, "icon_global")

    def test_state_round_trip_preserves_metrics_and_champion(self) -> None:
        system = hybrid.HybridSystem()
        features = system.outdoor_features(20.0, 22.0, 0.0, 6)
        system.update_outdoor("ecmwf_ifs", 6, features, 20.0, 22.0, 21.5, 21.0)
        system.champions[6] = "ecmwf_ifs"
        restored = hybrid.HybridSystem.from_dict(system.to_dict())
        self.assertEqual(restored.champions[6], "ecmwf_ifs")
        self.assertEqual(restored.metrics(6)["count"], 1)
        self.assertAlmostEqual(restored.metrics(6)["mae"], 0.5)

    def test_room_models_are_independent(self) -> None:
        system = hybrid.HybridSystem()
        features = system.room_features(20.0, 24.0, 0.1, 300.0, 3)
        before_a = system.predict_room("sensor.room_a", 3, 22.0, features)
        before_b = system.predict_room("sensor.room_b", 3, 22.0, features)
        system.update_room("sensor.room_a", 3, features, 22.0, before_a, 25.0)
        after_a = system.predict_room("sensor.room_a", 3, 22.0, features)
        after_b = system.predict_room("sensor.room_b", 3, 22.0, features)
        self.assertNotEqual(after_a, before_a)
        self.assertEqual(after_b, before_b)


if __name__ == "__main__":
    unittest.main()
