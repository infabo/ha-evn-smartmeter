"""EVN Smart Meter integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

FORCE_REIMPORT_KEY = f"{DOMAIN}_force_reimport"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EVN Smart Meter from a config entry."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    async def handle_reset_statistics(_call: ServiceCall) -> None:
        """Reset statistics and trigger a full reimport."""
        hass.data[FORCE_REIMPORT_KEY] = True
        _LOGGER.warning("Statistics reimport requested via service call")
        sensor = hass.data.get(f"{DOMAIN}_sensor")
        if sensor:
            await sensor.async_update()

    if not hass.services.has_service(DOMAIN, "reset_statistics"):
        hass.services.async_register(
            DOMAIN, "reset_statistics", handle_reset_statistics
        )

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        hass.data.get("evn_smartmeter_monthly_sensor", {}).pop(
            entry.entry_id, None
        )
    return unload_ok
