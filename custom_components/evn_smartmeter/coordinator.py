"""DataUpdateCoordinator for EVN Smart Meter."""

from __future__ import annotations

import logging
import zoneinfo
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.util.dt import as_utc
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
        if total <= 0 and last_date:
            _LOGGER.debug(
                "Skipping restore — no meaningful data (total=%.3f, last_date=%s)",
                total, last_date,
            )
            return
        if last_date:
            self._last_completed_date = date.fromisoformat(last_date)
            self._completed_days_total = total - current_day
            self._current_day_total = current_day
        else:
            self._completed_days_total = total
            self._current_day_total = 0.0
        _LOGGER.debug(
            "Restored state: completed=%.3f, current_day=%.3f, last_date=%s",
            self._completed_days_total, self._current_day_total, last_date,
        )

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

        today = date.today()
        yesterday = today - timedelta(days=1)

        # Process completed days (accounts for ~1-2 day data delay)
        await self._process_completed_days(yesterday)

        # Fetch today's running total (if available)
        try:
            today_data = await self.api.get_consumption_per_day(today)
            if today_data:
                non_null = [v for v in today_data if v is not None]
                self._current_day_total = sum(non_null) if non_null else 0.0
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
        """Process all completed days that haven't been counted yet.

        Data from the portal has ~1-2 day delay. We only advance
        _last_completed_date past a day if it had data or is old
        enough (>3 days) that data won't arrive anymore.
        """
        if self._last_completed_date is not None and self._last_completed_date >= up_to:
            return

        if self._last_completed_date is not None:
            start = self._last_completed_date + timedelta(days=1)
        else:
            # First run: go back far enough to find data despite delay
            start = up_to - timedelta(days=5)

        # Cap at 30 days to avoid excessive API calls
        earliest = up_to - timedelta(days=30)
        if start < earliest:
            start = earliest

        # Read the cumulative offset ONCE before the loop (like enelgrid).
        # async_add_external_statistics doesn't commit to DB immediately,
        # so reading per-day would return stale values.
        statistic_id = f"{DOMAIN}:consumption_15min"
        cumulative_offset = await self._get_last_cumulative_sum(statistic_id)
        _LOGGER.debug(
            "Starting day processing with cumulative offset: %.3f kWh",
            cumulative_offset,
        )

        last_advanced = self._last_completed_date
        current = start
        while current <= up_to:
            try:
                day_data = await self.api.get_consumption_per_day(current)
                non_null = [v for v in day_data if v is not None] if day_data else []
                if non_null:
                    day_total = sum(non_null)
                    self._completed_days_total += day_total
                    last_advanced = current
                    # Import 15-min statistics, tracking offset in memory
                    cumulative_offset = await self._import_15min_statistics(
                        current, day_data, cumulative_offset
                    )
                    _LOGGER.debug(
                        "Processed day %s: %.3f kWh (cumulative: %.3f)",
                        current.isoformat(), day_total, cumulative_offset,
                    )
                else:
                    # No data yet — only advance past this day if it's old enough
                    days_ago = (up_to - current).days
                    if days_ago >= 3:
                        last_advanced = current
                        _LOGGER.debug(
                            "Skipping day %s (no data, %d days old)",
                            current.isoformat(), days_ago,
                        )
                    else:
                        _LOGGER.debug(
                            "Day %s has no data yet (%d days ago), will retry",
                            current.isoformat(), days_ago,
                        )
                        # Stop advancing — retry this day next time
                        break
            except Exception:
                _LOGGER.warning(
                    "Failed to fetch data for %s, will retry", current.isoformat()
                )
                break
            current += timedelta(days=1)

        if last_advanced is not None:
            self._last_completed_date = last_advanced

    async def _import_15min_statistics(
        self, day: date, values: list[float | None], cumulative_offset: float
    ) -> float:
        """Import 15-min consumption values as external statistics.

        Args:
            day: The date to import.
            values: List of 96 consumption values (15-min intervals).
            cumulative_offset: The running cumulative sum from previous days.

        Returns:
            The updated cumulative sum after this day's data (for chaining).
        """
        tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
        midnight = datetime.combine(day, datetime.min.time(), tzinfo=tz)

        statistic_id = f"{DOMAIN}:consumption_15min"

        statistics: list[dict[str, Any]] = []
        cumulative = cumulative_offset

        for idx, value in enumerate(values):
            if value is not None:
                cumulative += value
                timestamp = midnight + timedelta(minutes=idx * 15)
                statistics.append({
                    "start": as_utc(timestamp),
                    "sum": cumulative,
                })

        if statistics:
            metadata = {
                "source": DOMAIN,
                "name": "EVN Smart Meter 15min Consumption",
                "statistic_id": statistic_id,
                "unit_of_measurement": "kWh",
                "has_mean": False,
                "has_sum": True,
            }
            try:
                async_add_external_statistics(self.hass, metadata, statistics)
                _LOGGER.info(
                    "Imported %d 15-min statistics for %s (offset=%.3f, final=%.3f)",
                    len(statistics),
                    day.isoformat(),
                    cumulative_offset,
                    cumulative,
                )
            except Exception as err:
                _LOGGER.warning(
                    "Failed to import 15-min statistics for %s: %s",
                    day.isoformat(),
                    err,
                )

        return cumulative

    async def _get_last_cumulative_sum(self, statistic_id: str) -> float:
        """Get the last recorded cumulative sum for a given statistic_id."""
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )

        if last_stats and statistic_id in last_stats:
            result = last_stats[statistic_id][0]["sum"]
            _LOGGER.debug(
                "Last cumulative sum for %s: %.3f", statistic_id, result
            )
            return result
        return 0.0
