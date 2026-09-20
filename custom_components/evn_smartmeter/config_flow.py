"""Config flow for EVN Smart Meter integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from . import statistic_id_for_username
from .smartmeter import Smartmeter
from .errors import SmartmeterLoginError, SmartmeterConnectionError

from .const import (
    DOMAIN,
    CONF_FETCH_HOUR_START,
    CONF_FETCH_HOUR_END,
    CONF_STATISTIC_ID,
    DEFAULT_FETCH_HOUR_START,
    DEFAULT_FETCH_HOUR_END,
    LEGACY_STATISTIC_ID,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})

_HOUR_SELECTOR = NumberSelector(
    NumberSelectorConfig(min=0, max=23, step=1, mode=NumberSelectorMode.SLIDER)
)


class EVNSmartmeterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EVN Smart Meter."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return EVNSmartmeterOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Prevent duplicate entries for same username
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            # Validate credentials
            error = await self._test_credentials(username, password)
            if error is None:
                # The first account keeps the legacy statistic id so existing
                # Energy Dashboard setups keep working; further accounts get
                # their own id derived from the username.
                taken = {
                    e.data.get(CONF_STATISTIC_ID)
                    for e in self._async_current_entries()
                }
                statistic_id = LEGACY_STATISTIC_ID
                if statistic_id in taken:
                    statistic_id = statistic_id_for_username(username)
                return self.async_create_entry(
                    title=f"EVN Smart Meter ({username})",
                    data={**user_input, CONF_STATISTIC_ID: statistic_id},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication after the portal rejected the credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password for the existing account."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        username = reauth_entry.data[CONF_USERNAME]

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            error = await self._test_credentials(username, password)
            if error is None:
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates={CONF_PASSWORD: password}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={"username": username},
            errors=errors,
        )

    async def _test_credentials(
        self, username: str, password: str
    ) -> str | None:
        """Validate credentials. Returns error key or None on success."""
        api = Smartmeter(self.hass, username, password)

        try:
            await api.authenticate()
        except SmartmeterLoginError:
            return "invalid_auth"
        except SmartmeterConnectionError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected exception during authentication")
            return "unknown"
        finally:
            await api.close()

        return None


class EVNSmartmeterOptionsFlow(OptionsFlow):
    """Options flow: configure the daily fetch time window."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            start = int(user_input[CONF_FETCH_HOUR_START])
            end = int(user_input[CONF_FETCH_HOUR_END])
            if start >= end:
                errors["base"] = "invalid_range"
            else:
                return self.async_create_entry(data=user_input)

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_FETCH_HOUR_START,
                    default=int(
                        current.get(CONF_FETCH_HOUR_START, DEFAULT_FETCH_HOUR_START)
                    ),
                ): _HOUR_SELECTOR,
                vol.Required(
                    CONF_FETCH_HOUR_END,
                    default=int(
                        current.get(CONF_FETCH_HOUR_END, DEFAULT_FETCH_HOUR_END)
                    ),
                ): _HOUR_SELECTOR,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
