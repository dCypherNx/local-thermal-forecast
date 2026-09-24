"""Config and options flows for Local Thermal Forecast."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME, Platform
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_EXTERNAL_SENSORS,
    CONF_RETENTION_DAYS,
    CONF_ROOM_SENSORS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    MAX_UPDATE_INTERVAL_MINUTES,
    MIN_UPDATE_INTERVAL_MINUTES,
)


def _temperature_selector(*, multiple: bool) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=Platform.SENSOR,
            device_class=SensorDeviceClass.TEMPERATURE,
            multiple=multiple,
        )
    )


def _config_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): str,
            vol.Required(CONF_LATITUDE, default=defaults[CONF_LATITUDE]): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-90,
                    max=90,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_LONGITUDE, default=defaults[CONF_LONGITUDE]): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-180,
                    max=180,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_EXTERNAL_SENSORS,
                default=defaults.get(CONF_EXTERNAL_SENSORS, []),
            ): _temperature_selector(multiple=True),
            vol.Optional(
                CONF_ROOM_SENSORS,
                default=defaults.get(CONF_ROOM_SENSORS, []),
            ): _temperature_selector(multiple=True),
        }
    )


def _sensor_errors(user_input: dict[str, Any]) -> dict[str, str]:
    if not user_input[CONF_EXTERNAL_SENSORS]:
        return {CONF_EXTERNAL_SENSORS: "external_sensor_required"}
    if set(user_input[CONF_EXTERNAL_SENSORS]) & set(user_input.get(CONF_ROOM_SENSORS, [])):
        return {"base": "sensor_roles_overlap"}
    return {}


class LocalThermalForecastConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup and reconfiguration."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the single home config entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _sensor_errors(user_input)
            if not errors:
                await self.async_set_unique_id("home")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        defaults = {
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
            **(user_input or {}),
        }
        return self.async_show_form(
            step_id="user", data_schema=_config_schema(defaults), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure location and source sensors."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _sensor_errors(user_input)
            if not errors:
                await self.async_set_unique_id("home")
                self._abort_if_unique_id_mismatch()
                return self.async_update_and_abort(
                    entry,
                    data_updates=user_input,
                    title=user_input[CONF_NAME],
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_config_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options handler."""
        return LocalThermalForecastOptionsFlow()


class LocalThermalForecastOptionsFlow(OptionsFlow):
    """Manage operational tuning without changing identity."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Configure retention and polling interval."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RETENTION_DAYS,
                    default=current.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=7,
                        max=365,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_UPDATE_INTERVAL_MINUTES,
                        max=MAX_UPDATE_INTERVAL_MINUTES,
                        step=15,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
