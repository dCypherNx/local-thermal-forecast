"""Provider-neutral forecast boundary."""

from __future__ import annotations

from typing import Protocol

from .models import ForecastBundle


class ForecastProviderError(Exception):
    """Raised when a provider cannot supply a usable forecast."""


class ForecastProvider(Protocol):
    """Interface implemented by weather forecast providers."""

    async def async_fetch(self, latitude: float, longitude: float) -> ForecastBundle:
        """Fetch a normalized forecast bundle."""
