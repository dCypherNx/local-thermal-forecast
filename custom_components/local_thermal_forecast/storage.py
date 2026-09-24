"""Versioned persistence for forecasts and learned state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_PREFIX, STORAGE_VERSION


class ThermalStore:
    """Persist compact forecast snapshots and model state per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str, retention_days: int) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry_id}"
        )
        self._retention_days = retention_days
        self.data: dict[str, Any] = {
            "schema_version": 3,
            "snapshots": [],
            "hybrid_state": {},
            "last_data": None,
        }

    async def async_load(self) -> dict[str, Any]:
        """Load persisted data."""
        loaded = await self._store.async_load()
        if loaded and loaded.get("schema_version", 1) < 2:
            old_rooms = loaded.get("hybrid_state", {}).get("rooms", {})
            self.data = {
                "schema_version": 3,
                "snapshots": [],
                "hybrid_state": {
                    "outdoor": {},
                    "rooms": old_rooms,
                    "champions": {},
                },
                "last_data": None,
            }
            await self._store.async_save(self.data)
        elif loaded and loaded.get("schema_version", 1) < 3:
            self.data.update(loaded)
            self.data["schema_version"] = 3
            self.data["snapshots"] = []
            await self._store.async_save(self.data)
        elif loaded:
            self.data.update(loaded)
        self.prune()
        return self.data

    @property
    def snapshots(self) -> list[dict[str, Any]]:
        """Return the mutable forecast ledger."""
        return self.data.setdefault("snapshots", [])

    @callback
    def prune(self) -> None:
        """Remove forecast snapshots outside the retention period."""
        cutoff = datetime.now(UTC) - timedelta(days=self._retention_days)
        self.data["snapshots"] = [
            snapshot
            for snapshot in self.snapshots
            if datetime.fromisoformat(snapshot["issued_at"]) >= cutoff
        ]

    @callback
    def async_delay_save(self) -> None:
        """Schedule a coalesced write."""
        self.prune()
        self._store.async_delay_save(lambda: self.data, 10.0)

    async def async_save(self) -> None:
        """Immediately save pending state during unload."""
        self.prune()
        await self._store.async_save(self.data)
