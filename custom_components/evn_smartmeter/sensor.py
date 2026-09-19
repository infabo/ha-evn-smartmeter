"""Sensor platform for EVN Smart Meter integration.

Imports consumption data from the EVN/Netz NÖ Smart Meter portal as
external statistics, following the pattern used by the official elvia
integration (homeassistant/components/elvia/importer.py).
"""

import asyncio
import logging
import random
from datetime import date, datetime, time as dt_time, timedelta
from typing import cast

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.components.recorder.util import get_instance
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from . import EVNRuntimeData
from .const import (
    DOMAIN,
    CONF_FETCH_HOUR_START,
    CONF_FETCH_HOUR_END,
    CONF_STATISTIC_ID,
    DEFAULT_FETCH_HOUR_START,
    DEFAULT_FETCH_HOUR_END,
)
from .errors import SmartmeterConnectionError, SmartmeterLoginError
from .smartmeter import Smartmeter

_LOGGER = logging.getLogger(__name__)

_EPOCH = dt_util.utc_from_timestamp(0)


def _local_day_start_utc(day: date) -> datetime:
    """Return local midnight of `day` (HA time zone) as a UTC datetime."""
    return dt_util.as_utc(dt_util.start_of_local_day(day))


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up EVN Smart Meter sensors from a config entry."""
    consumption_sensor = EVNSmartmeterSensor(hass, entry)
    monthly_sensor = EVNSmartmeterMonthlySensor(entry)

    # Shared with the reset service and the import sensor
    entry.runtime_data = EVNRuntimeData(
        import_sensor=consumption_sensor, monthly_sensor=monthly_sensor
    )

    async_add_entities([consumption_sensor, monthly_sensor])

    # Schedule daily fetch at a random time within the configured window
    _schedule_next_fetch(hass, entry, consumption_sensor)


def _schedule_next_fetch(hass, entry, consumption_sensor):
    """Schedule the next fetch at a random time within [fetch_hour_start, fetch_hour_end)."""
    hour_start = int(entry.options.get(CONF_FETCH_HOUR_START, DEFAULT_FETCH_HOUR_START))
    hour_end = int(entry.options.get(CONF_FETCH_HOUR_END, DEFAULT_FETCH_HOUR_END))

    if hour_end > hour_start:
        offset_minutes = random.randint(0, (hour_end - hour_start) * 60 - 1)
    else:
        offset_minutes = 0

    fetch_hour = hour_start + offset_minutes // 60
    fetch_minute = offset_minutes % 60

    now = dt_util.now()
    fetch_dt = dt_util.as_local(
        datetime.combine(now.date(), dt_time(hour=fetch_hour, minute=fetch_minute))
    )
    if fetch_dt <= now:
        fetch_dt = dt_util.as_local(
            datetime.combine(
                now.date() + timedelta(days=1),
                dt_time(hour=fetch_hour, minute=fetch_minute),
            )
        )

    async def _run(_now):
        success = await consumption_sensor.async_update()
        if not success:
            _schedule_retry(hass, entry, consumption_sensor, attempt=1)
        else:
            _schedule_next_fetch(hass, entry, consumption_sensor)

    unsub = async_track_point_in_time(hass, _run, fetch_dt)
    entry.async_on_unload(unsub)
    _LOGGER.debug("Next EVN fetch scheduled at %s", fetch_dt.isoformat())


_MAX_RETRIES = 2
_RETRY_DELAY_MINUTES = 30

# First import: stop walking back after this many consecutive months without
# any data. A single empty month (portal outage, meter swap, or the current
# month not yet published) must not end the history search.
_EMPTY_MONTHS_TO_STOP = 3
# Hard upper bound for the first import, in months.
_MAX_HISTORY_MONTHS = 60


def _schedule_retry(hass, entry, consumption_sensor, attempt: int):
    """Retry a failed fetch up to _MAX_RETRIES times, _RETRY_DELAY_MINUTES apart."""
    if attempt > _MAX_RETRIES:
        _LOGGER.warning(
            "EVN fetch failed after %d retries; giving up until next scheduled run",
            _MAX_RETRIES,
        )
        _schedule_next_fetch(hass, entry, consumption_sensor)
        return

    retry_at = dt_util.now() + timedelta(minutes=_RETRY_DELAY_MINUTES)
    _LOGGER.warning(
        "EVN connection error; scheduling retry %d/%d at %s",
        attempt,
        _MAX_RETRIES,
        retry_at.strftime("%H:%M"),
    )

    async def _run(_now):
        success = await consumption_sensor.async_update()
        if not success:
            _schedule_retry(hass, entry, consumption_sensor, attempt + 1)
        else:
            _schedule_next_fetch(hass, entry, consumption_sensor)

    unsub = async_track_point_in_time(hass, _run, retry_at)
    entry.async_on_unload(unsub)


class EVNSmartmeterSensor(SensorEntity):
    """Import sensor: fetches EVN data and saves to HA external statistics."""

    _attr_should_poll = False

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.entry_id = entry.entry_id
        self._username = entry.data[CONF_USERNAME]
        self._password = entry.data[CONF_PASSWORD]
        self._statistic_id = entry.data[CONF_STATISTIC_ID]
        self._attr_name = "EVN Smart Meter Import"
        self._attr_unique_id = f"{entry.entry_id}_import"
        self._attr_native_value = None
        # Serialises runs started by the timer, the retry chain, the startup
        # fetch and the reset service so they never share a client or write
        # overlapping statistics with different baselines.
        self._lock = asyncio.Lock()
        # Set by the reset_statistics service before calling async_update()
        self.force_reimport = False

    async def async_added_to_hass(self) -> None:
        """Start the first import once the entity is registered."""
        await super().async_added_to_hass()
        # Immediately fetch data on startup / reload
        self.entry.async_create_task(self.hass, self.async_update())

    def _set_status(self, status: str) -> None:
        """Update the import status and publish it to Home Assistant."""
        self._attr_native_value = status
        self.async_write_ha_state()

    async def _fetch_days(self, api: Smartmeter, start, end):
        """Fetch consumption data for a date range (day by day).

        Days without any values are skipped. Connection and login errors
        propagate to the caller so the run is marked as failed and retried
        instead of being mistaken for missing data.
        """
        data = {}
        current = start
        while current <= end:
            values = await api.get_consumption_per_day(current)
            non_null = [v for v in values if v is not None] if values else []
            if non_null:
                data[current] = values
                _LOGGER.debug(
                    "Day %s: %d non-null values, sum=%.3f kWh",
                    current.isoformat(),
                    len(non_null),
                    sum(non_null),
                )
            else:
                _LOGGER.debug("No data for %s", current.isoformat())
            current += timedelta(days=1)
        return data

    async def async_update(self) -> bool:
        """Fetch EVN data and save to HA statistics.

        Returns True when the run completed (success or permanent error that
        should not be retried), False on transient connection errors.

        First import: walks backwards month by month until several
        consecutive months are empty. Subsequent imports: re-fetch from the
        day of the last known statistic up to yesterday.
        """
        async with self._lock:
            return await self._async_run_import()

    async def _async_run_import(self) -> bool:
        """Run one import. Caller must hold self._lock."""
        api = Smartmeter(self._username, self._password)
        try:
            await api.authenticate()
            await api.get_meter_details()

            # Determine fetch range (elvia pattern)
            statistic_id = self._statistic_id
            recorder = get_instance(self.hass)

            force_reimport = self.force_reimport
            self.force_reimport = False

            if force_reimport:
                last_stats = None
                _LOGGER.warning("Force reimport — ignoring existing stats")
            else:
                last_stats = await recorder.async_add_executor_job(
                    get_last_statistics, self.hass, 1, statistic_id, True, {"sum"},
                )

            today = dt_util.now().date()
            yesterday = today - timedelta(days=1)

            if not last_stats:
                # First import: go back month by month. The search ends after
                # _EMPTY_MONTHS_TO_STOP consecutive months without data or at
                # _MAX_HISTORY_MONTHS, so a single empty month (e.g. the
                # current month before the portal publishes it) does not cut
                # off the history.
                _LOGGER.info("First import: fetching all available history")
                all_day_data = {}
                month_start = yesterday.replace(day=1)
                empty_months = 0

                for _ in range(_MAX_HISTORY_MONTHS):
                    month_last = (
                        (month_start.replace(day=28) + timedelta(days=4))
                        .replace(day=1)
                        - timedelta(days=1)
                    )
                    month_end = min(month_last, yesterday)
                    month_data = await self._fetch_days(api, month_start, month_end)

                    if month_data:
                        empty_months = 0
                        all_day_data.update(month_data)
                        _LOGGER.info(
                            "Fetched %d days for %s",
                            len(month_data),
                            month_start.strftime("%Y-%m"),
                        )
                    else:
                        empty_months += 1
                        _LOGGER.info(
                            "No data for %s (%d/%d empty months)",
                            month_start.strftime("%Y-%m"),
                            empty_months,
                            _EMPTY_MONTHS_TO_STOP,
                        )
                        if empty_months >= _EMPTY_MONTHS_TO_STOP:
                            _LOGGER.info("History complete")
                            break

                    # Previous month
                    month_start = (
                        month_start - timedelta(days=1)
                    ).replace(day=1)
                else:
                    _LOGGER.info(
                        "Reached history limit of %d months", _MAX_HISTORY_MONTHS
                    )
            else:
                # Incremental: re-fetch the local day of the last known stat
                # (its values may still have been partial) up to yesterday
                last_start_ts = last_stats[statistic_id][0]["start"]
                start_date = dt_util.as_local(
                    dt_util.utc_from_timestamp(last_start_ts)
                ).date()
                _LOGGER.info(
                    "Incremental import: %d new days from %s",
                    (today - start_date).days,
                    start_date.isoformat(),
                )
                all_day_data = await self._fetch_days(api, start_date, yesterday)

            if all_day_data:
                await self.save_to_home_assistant(
                    all_day_data, last_stats, clear_existing=force_reimport
                )
                self._set_status("Imported")
                _LOGGER.warning(
                    "EVN import complete: %d days imported", len(all_day_data)
                )
            else:
                _LOGGER.warning("No consumption data found")
                self._set_status("No data")

            # Always refresh the monthly value from stored statistics so it
            # is correct after restarts and on runs without new data.
            await self._async_update_monthly()

            return True

        except SmartmeterLoginError:
            _LOGGER.error("EVN login failed, check credentials")
            self._set_status("Login error")
            return True  # permanent error — do not retry
        except SmartmeterConnectionError as err:
            _LOGGER.warning("Connection error: %s", err)
            self._set_status("Connection error")
            return False  # transient — caller may retry
        except Exception as err:
            _LOGGER.exception("Failed to update EVN data: %s", err)
            self._set_status("Error")
            return True  # unknown — do not retry
        finally:
            await api.close()

    async def _get_sum_before(self, statistic_id: str, before: datetime) -> float:
        """Return the cumulative sum of the last statistic strictly before `before`.

        Checks the two preceding days first (the common case), then falls
        back to the whole history so that a gap of any length between the
        last stored hour and the import window cannot reset the sum to 0.
        """
        recorder = get_instance(self.hass)
        for start, period in (
            (before - timedelta(days=2), "hour"),
            (_EPOCH, "month"),
        ):
            stats = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                before,
                {statistic_id},
                period,
                None,
                {"sum"},
            )
            rows = stats.get(statistic_id) if stats else None
            if rows and rows[-1].get("sum") is not None:
                return cast(float, rows[-1]["sum"])
        return 0.0

    async def save_to_home_assistant(
        self, all_data_by_date, last_stats, clear_existing: bool = False
    ):
        """Save consumption data as external statistics (elvia pattern).

        1. Determine the cumulative sum baseline from the last statistic
           before the import window (or 0 on a fresh import)
        2. Aggregate 15-min EVN intervals into hourly buckets
        3. Build one list of StatisticData, call async_add_external_statistics once

        With clear_existing=True the stored statistic is dropped first so a
        forced reimport cannot leave stale rows with a higher sum behind.
        """
        statistic_id = self._statistic_id
        recorder = get_instance(self.hass)

        earliest_day = min(all_data_by_date.keys())
        window_start = _local_day_start_utc(earliest_day)

        if clear_existing:
            _LOGGER.warning("Clearing existing statistics for %s", statistic_id)
            recorder.async_clear_statistics([statistic_id])

        if not last_stats:
            _sum = 0.0
            _LOGGER.debug("First import, starting sum at 0.0")
        else:
            _sum = await self._get_sum_before(statistic_id, window_start)
            _LOGGER.debug("Resuming sum from %.3f kWh", _sum)

        # Build all statistics in one list
        statistics: list[StatisticData] = []
        for day_date, values in sorted(all_data_by_date.items()):
            # EVN provides 15-min intervals; HA requires hourly timestamps.
            # Bucket by elapsed hours since local midnight and add them to
            # the UTC day start. On DST days the portal delivers 92 or 100
            # intervals; counting elapsed time keeps every hour unique and
            # avoids the non-existent / ambiguous local wall-clock hours.
            day_start = _local_day_start_utc(day_date)
            hourly_sums: dict[int, float] = {}
            for idx, value in enumerate(values):
                if value is not None:
                    hour = idx // 4
                    hourly_sums[hour] = hourly_sums.get(hour, 0.0) + value

            for hour in sorted(hourly_sums):
                _sum += hourly_sums[hour]
                ts = day_start + timedelta(hours=hour)
                statistics.append(StatisticData(start=ts, sum=_sum))

        if statistics:
            try:
                async_add_external_statistics(
                    self.hass,
                    StatisticMetaData(
                        mean_type=StatisticMeanType.NONE,
                        has_sum=True,
                        name=f"EVN Smart Meter Consumption ({self._username})",
                        source=DOMAIN,
                        statistic_id=statistic_id,
                        unit_class="energy",
                        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                    ),
                    statistics,
                )
                _LOGGER.info(
                    "Imported %d hourly statistics (sum=%.3f kWh)",
                    len(statistics),
                    _sum,
                )
            except HomeAssistantError as err:
                _LOGGER.exception("Failed to save statistics: %s", err)
                raise

    async def _async_update_monthly(self) -> None:
        """Recompute the monthly consumption sensor from stored statistics.

        month total = last stored cumulative sum - sum before local month
        start. Reading from the recorder instead of the last fetch makes the
        value independent of how many days the run imported.
        """
        monthly_sensor = self.entry.runtime_data.monthly_sensor

        try:
            statistic_id = self._statistic_id
            recorder = get_instance(self.hass)
            # Statistics queued by this run must be committed before reading.
            await recorder.async_block_till_done()

            last_stats = await recorder.async_add_executor_job(
                get_last_statistics, self.hass, 1, statistic_id, True, {"sum"},
            )
            month_start = _local_day_start_utc(dt_util.now().date().replace(day=1))

            total_kwh = 0.0
            rows = last_stats.get(statistic_id) if last_stats else None
            if rows and rows[0].get("sum") is not None:
                last_row = rows[0]
                if last_row["start"] >= month_start.timestamp():
                    sum_before_month = await self._get_sum_before(
                        statistic_id, month_start
                    )
                    total_kwh = cast(float, last_row["sum"]) - sum_before_month

            monthly_sensor.set_total(total_kwh)
            _LOGGER.info("Updated monthly sensor to %.3f kWh", total_kwh)
        except Exception as err:
            _LOGGER.warning("Failed to update monthly sensor: %s", err)


class EVNSmartmeterMonthlySensor(SensorEntity):
    """Monthly cumulative total sensor."""

    def __init__(self, entry):
        # Kept as the suggested object id so existing installations keep
        # their entity id; the registry appends a suffix for further accounts.
        self.entity_id = "sensor.evn_smartmeter_monthly_consumption"
        self._attr_unique_id = f"{entry.entry_id}_monthly"
        self._attr_name = "EVN Smart Meter Monthly Consumption"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = "total_increasing"
        self._attr_native_unit_of_measurement = "kWh"
        self._state = 0
        self._attr_extra_state_attributes = {"source": "evn_smartmeter"}

    @property
    def state(self):
        return self._state

    def set_total(self, new_total):
        self._state = round(new_total, 3)
        self.async_write_ha_state()

