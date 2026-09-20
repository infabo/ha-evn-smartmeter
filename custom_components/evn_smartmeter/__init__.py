"""EVN Smart Meter integration for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.util import slugify

from .const import CONF_STATISTIC_ID, DOMAIN, LEGACY_STATISTIC_ID

if TYPE_CHECKING:
    from .sensor import EVNSmartmeterMonthlySensor, EVNSmartmeterSensor

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


@dataclass
class EVNRuntimeData:
    """Per-entry objects shared between platform and services."""

    import_sensor: EVNSmartmeterSensor
    monthly_sensor: EVNSmartmeterMonthlySensor


type EVNConfigEntry = ConfigEntry[EVNRuntimeData]


def statistic_id_for_username(username: str) -> str:
    """Build a per-account statistic id (used when the legacy id is taken)."""
    return f"{DOMAIN}:consumption_{slugify(username)}"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries."""
    if entry.version == 1:
        # Version 1 entries all wrote to LEGACY_STATISTIC_ID. The first entry
        # keeps it; any further entry gets its own id so the accounts stop
        # overwriting each other's statistics.
        taken = {
            other.data.get(CONF_STATISTIC_ID)
            for other in hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != entry.entry_id
        }
        statistic_id = LEGACY_STATISTIC_ID
        if statistic_id in taken:
            statistic_id = statistic_id_for_username(entry.data["username"])
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_STATISTIC_ID: statistic_id}, version=2
        )
        _LOGGER.info(
            "Migrated %s to version 2 with statistic id %s", entry.title, statistic_id
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EVNConfigEntry) -> bool:
    """Set up EVN Smart Meter from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def handle_reset_statistics(_call: ServiceCall) -> None:
        """Reset statistics and trigger a full reimport for every account.

        Each import is queued as a background task of its config entry, so
        the service returns immediately and an unload can cancel the work.
        """
        _LOGGER.warning("Statistics reimport requested via service call")
        for loaded in hass.config_entries.async_loaded_entries(DOMAIN):
            loaded.runtime_data.import_sensor.async_request_reimport()

    if not hass.services.has_service(DOMAIN, "reset_statistics"):
        hass.services.async_register(
            DOMAIN, "reset_statistics", handle_reset_statistics
        )

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EVNConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and not any(
        loaded.entry_id != entry.entry_id
        for loaded in hass.config_entries.async_loaded_entries(DOMAIN)
    ):
        hass.services.async_remove(DOMAIN, "reset_statistics")
    return unload_ok
