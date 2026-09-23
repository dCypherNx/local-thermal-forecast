"""Load pure integration modules without installing Home Assistant."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType


def load_module(name: str):
    root = Path(__file__).parents[1]
    custom_components = root / "custom_components"
    integration = custom_components / "local_thermal_forecast"
    for package_name, path in (
        ("custom_components", custom_components),
        ("custom_components.local_thermal_forecast", integration),
    ):
        if package_name not in sys.modules:
            package = ModuleType(package_name)
            package.__path__ = [str(path)]  # type: ignore[attr-defined]
            sys.modules[package_name] = package
    return importlib.import_module(f"custom_components.local_thermal_forecast.{name}")
