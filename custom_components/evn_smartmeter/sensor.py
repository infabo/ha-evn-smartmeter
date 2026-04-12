"""Sensor platform for EVN Smart Meter integration.

Architecture follows enelgrid: a single sensor entity that fetches data from
the portal and imports it as external statistics via async_add_external_statistics.
The sensor state is just a status indicator ("Imported" / "No data" / "Error").
All consumption data lives in HA statistics and is viewable in the Energy Dashboard.
"""

from __future__ import annotations

import logging
import zoneinfo
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util.dt import as_utc

from .const import DOMAIN, SCAN_INTERVAL_HOURS
from .errors import SmartmeterLoginError, SmartmeterConnectionError
from .smartmeter import Smartmeter

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=SCAN_INTERVAL_HOURS)
LOOKBACK_DAYS = 7


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVN Smart Meter sensors from a config entry."""
    monthly_sensor = EVNSmartmeterMonthlySensor(entry)
    import_sensor = EVNSmartmeterSensor(hass, entry, monthly_sensor)
    async_add_entities([import_sensor, monthly_sensor])

    # Initial data fetch
    hass.async_create_task(import_sensor.async_update())

    # Schedule periodic updates
    async def _scheduled_update(_now: Any) -> None:
        await import_sensor.async_update()

    async_track_time_interval(hass, _scheduled_update, SCAN_INTERVAL)


class EVNSmartmeterSensor(SensorEntity):
    """Sensor that fetches EVN smart meter data and imports it to HA statistics.

    The sensor state is a status string (e.g. "Imported", "No data", "Error").
    Actual consumption data is stored as external statistics and viewable
    in the Energy Dashboard and Developer Tools → Statistics.
    """

    _attr_has_entity_name = True
    _attr_name = "EVN Smart Meter Import"

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry,
        monthly_sensor: EVNSmartmeterMonthlySensor,
    ) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._entry = entry
        self._monthly_sensor = monthly_sensor
        self._attr_unique_id = f"{entry.entry_id}_import"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="EVN Smart Meter",
            manufacturer="Netz Niederösterreich",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._state: str | None = None
        self.api = Smartmeter(
            entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
        )
        self._metering_point_id: str | None = None

    @property
    def native_value(self) -> str | None:
        """Return the import status."""
        return self._state

    async def async_update(self) -> None:
        """Fetch consumption data from portal and import to HA statistics."""
        try:
            await self.api.authenticate()

            if self._metering_point_id is None:
                meters = await self.api.get_meter_details()
                self._metering_point_id = meters[0].get("meteringPointId")

            today = date.today()

            # Fetch last N days (accounts for 1-2 day portal delay)
            all_day_data: dict[date, list[float | None]] = {}
            for i in range(LOOKBACK_DAYS, 0, -1):
                day = today - timedelta(days=i)
                try:
                    values = await self.api.get_consumption_per_day(day)
                    non_null = [v for v in values if v is not None] if values else []
                    if non_null:
                        all_day_data[day] = values
                except Exception:
                    _LOGGER.debug("No data available for %s", day.isoformat())

            if all_day_data:
                imported = await self._save_statistics(all_day_data)
                self._state = f"{imported} days imported"
                # Update monthly sensor with current month's total
                self._update_monthly(all_day_data)
            else:
                self._state = "No data"

        except SmartmeterLoginError:
            self._state = "Login error"
            raise ConfigEntryAuthFailed(
                "Authentication failed. Check username/password."
            )
        except SmartmeterConnectionError as err:
            _LOGGER.warning("Connection error: %s", err)
            self._state = "Connection error"
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            _LOGGER.exception("Failed to update EVN data: %s", err)
            self._state = "Error"

    async def _save_statistics(
        self, all_data_by_date: dict[date, list[float | None]]
    ) -> int:
        """Save consumption data to HA external statistics.

        Follows enelgrid pattern:
        1. Read cumulative offset ONCE from DB
        2. Only import days that are NEW (after last imported timestamp)
        3. Track cumulative sum in memory across days

        Returns the number of days imported.
        """
        statistic_id = f"{DOMAIN}:consumption"
        tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)

        # Read last imported state from DB
        cumulative, last_imported_date = await self._get_last_state(statistic_id)

        # Filter to only NEW days (after last imported data)
        sorted_days = sorted(all_data_by_date.keys())
        if last_imported_date is not None:
            sorted_days = [d for d in sorted_days if d > last_imported_date]

        if not sorted_days:
            _LOGGER.debug("No new days to import (last imported: %s)", last_imported_date)
            return 0

        metadata = {
            "source": DOMAIN,
            "name": "EVN Smart Meter Consumption",
            "statistic_id": statistic_id,
            "unit_of_measurement": "kWh",
            "has_mean": False,
            "has_sum": True,
        }

        days_imported = 0
        for day in sorted_days:
            values = all_data_by_date[day]
            midnight = datetime.combine(day, datetime.min.time(), tzinfo=tz)
            stats: list[dict[str, Any]] = []

            for idx, value in enumerate(values):
                if value is not None:
                    cumulative += value
                    ts = midnight + timedelta(minutes=idx * 15)
                    stats.append({"start": as_utc(ts), "sum": cumulative})

            if stats:
                async_add_external_statistics(self.hass, metadata, stats)
                days_imported += 1
                _LOGGER.info(
                    "Imported %d points for %s (cumulative=%.3f kWh)",
                    len(stats), day.isoformat(), cumulative,
                )

        return days_imported

    async def _get_last_state(
        self, statistic_id: str
    ) -> tuple[float, date | None]:
        """Get last cumulative sum and date from statistics DB.

        Returns (cumulative_sum, last_date) tuple.
        """
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )

        if last_stats and statistic_id in last_stats:
            entry = last_stats[statistic_id][0]
            cumulative = entry["sum"]
            # Extract date from the last statistic's start timestamp
            last_start = entry.get("start")
            last_date = None
            if last_start is not None:
                if isinstance(last_start, (int, float)):
                    last_date = datetime.fromtimestamp(
                        last_start, tz=zoneinfo.ZoneInfo(self.hass.config.time_zone)
                    ).date()
                elif isinstance(last_start, datetime):
                    last_date = last_start.date()
            _LOGGER.debug(
                "Last state: sum=%.3f, date=%s", cumulative, last_date
            )
            return cumulative, last_date

        return 0.0, None

    def _update_monthly(
        self, all_data_by_date: dict[date, list[float | None]]
    ) -> None:
        """Sum up current month's fetched data and update monthly sensor."""
        today = date.today()
        month_total = 0.0
        for day, values in all_data_by_date.items():
            if day.year == today.year and day.month == today.month:
                month_total += sum(v for v in values if v is not None)
        self._monthly_sensor.set_total(month_total)


class EVNSmartmeterMonthlySensor(SensorEntity):
    """Monthly cumulative consumption sensor."""

    _attr_has_entity_name = True
    _attr_name = "Monthly Consumption"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = "total_increasing"
    _attr_native_unit_of_measurement = "kWh"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the monthly sensor."""
        self._attr_unique_id = f"{entry.entry_id}_monthly"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="EVN Smart Meter",
            manufacturer="Netz Niederösterreich",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._state: float = 0

    @property
    def native_value(self) -> float:
        """Return the monthly total."""
        return self._state

    def set_total(self, total: float) -> None:
        """Update monthly total and write state."""
        self._state = round(total, 3)
        self.async_write_ha_state()
