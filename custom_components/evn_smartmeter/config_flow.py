"""Config flow for EVN Smart Meter integration."""

from __future__ import annotations

import logging
import tempfile
import os
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

from pynoesmartmeter import Smartmeter
from pynoesmartmeter.errors import SmartmeterLoginError, SmartmeterConnectionError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class EVNSmartmeterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EVN Smart Meter."""

    VERSION = 1

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
                return self.async_create_entry(
                    title=f"EVN Smart Meter ({username})",
                    data=user_input,
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def _test_credentials(
        self, username: str, password: str
    ) -> str | None:
        """Validate credentials. Returns error key or None on success."""
        api = Smartmeter(username, password)
        api.SESSION_FILE = os.path.join(
            tempfile.gettempdir(), "evn_smartmeter_configflow.pkl"
        )

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
            if api._session is not None:
                await api._session.aclose()
            # Clean up temp session file
            if os.path.exists(api.SESSION_FILE):
                os.remove(api.SESSION_FILE)

        return None
