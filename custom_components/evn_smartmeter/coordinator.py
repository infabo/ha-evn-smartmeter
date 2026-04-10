"""DataUpdateCoordinator for EVN Smart Meter."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .smartmeter import Smartmeter
from .errors import SmartmeterLoginError, SmartmeterConnectionError

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL_MINUTES

_LOGGER = logging.getLogger(__name__)


class EVNSmartmeterCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to fetch EVN Smart Meter consumption data."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES),
            config_entry=entry,
        )

        username = entry.data[CONF_USERNAME]
        password = entry.data[CONF_PASSWORD]

        self.api = Smartmeter(username, password)

        # Accumulated total of all fully completed days
        self._completed_days_total: float = 0.0
        # Running total for today (recalculated each update)
        self._current_day_total: float = 0.0
        # Last date that was fully processed into completed_days_total
        self._last_completed_date: date | None = None
        # Meter metadata
        self._meter_id: str | None = None
        self._metering_point_id: str | None = None

    @property
    def total_consumption(self) -> float:
        """Total accumulated consumption in kWh."""
        return self._completed_days_total + self._current_day_total

    @property
    def daily_consumption(self) -> float:
        """Today's consumption so far in kWh."""
        return self._current_day_total

    @property
    def last_completed_date_str(self) -> str | None:
        """Last fully processed date as ISO string."""
        if self._last_completed_date is not None:
            return self._last_completed_date.isoformat()
        return None

    @property
    def meter_id(self) -> str | None:
        """Return the meter ID."""
        return self._meter_id

    def restore_state(
        self,
        total: float,
        last_date: str | None,
        current_day: float = 0.0,
    ) -> None:
        """Restore state from a previous session."""
        if last_date:
            self._last_completed_date = date.fromisoformat(last_date)
            # The restored total includes completed days + last known current_day
            # On restore, all of the restored total goes to completed_days
            self._completed_days_total = total - current_day
            self._current_day_total = current_day
        else:
            self._completed_days_total = total
            self._current_day_total = 0.0

    async def async_shutdown(self) -> None:
        """Clean up resources."""
        await super().async_shutdown()
        await self.api.close()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from EVN Smart Meter API."""
        try:
            await self.api.authenticate()
        except SmartmeterLoginError as err:
            raise ConfigEntryAuthFailed(
                "Authentication failed. Check username/password."
            ) from err
        except SmartmeterConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err

        # Load meter info on first run
        if self._metering_point_id is None:
            try:
                meters = await self.api.get_meter_details()
                meter = meters[0]
                self._metering_point_id = meter.get("meteringPointId")
                self._meter_id = meter.get("meteringPointId")
            except Exception as err:
                raise UpdateFailed(f"Error fetching meter details: {err}") from err

        yesterday = date.today() - timedelta(days=1)

        # Process any unprocessed completed days (up to yesterday)
        await self._process_completed_days(yesterday)

        # Fetch today's running total (15-min intervals available so far)
        today = date.today()
        try:
            today_data = await self.api.get_consumption_per_day(today)
            if today_data:
                self._current_day_total = sum(
                    v for v in today_data if v is not None
                )
            else:
                self._current_day_total = 0.0
        except Exception:
            _LOGGER.debug("Today's data not yet available, keeping previous value")

        return {
            "total_consumption": self.total_consumption,
            "daily_consumption": self._current_day_total,
            "last_completed_date": self.last_completed_date_str,
            "meter_id": self._meter_id,
        }

    async def _process_completed_days(self, up_to: date) -> None:
        """Process all completed days that haven't been counted yet."""
        if self._last_completed_date is not None and self._last_completed_date >= up_to:
            return

        start = (
            self._last_completed_date + timedelta(days=1)
            if self._last_completed_date is not None
            else up_to
        )

        # Cap at 30 days to avoid excessive API calls on first run
        earliest = up_to - timedelta(days=30)
        if start < earliest:
            start = earliest

        current = start
        while current <= up_to:
            try:
                day_data = await self.api.get_consumption_per_day(current)
                if day_data:
                    day_total = sum(v for v in day_data if v is not None)
                    self._completed_days_total += day_total
                    _LOGGER.debug(
                        "Processed day %s: %.3f kWh", current.isoformat(), day_total
                    )
            except Exception:
                _LOGGER.warning("Failed to fetch data for %s, skipping", current.isoformat())
            current += timedelta(days=1)

        self._last_completed_date = up_to
