"""Sensor platform for EVN Smart Meter integration.

Imports consumption data from the EVN/Netz NÖ Smart Meter portal as
external statistics, following the pattern used by the official elvia
integration (homeassistant/components/elvia/importer.py).
"""

import logging
from datetime import date, datetime, timedelta
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
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .errors import SmartmeterConnectionError, SmartmeterLoginError
from .smartmeter import Smartmeter

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up EVN Smart Meter sensors from a config entry."""
    consumption_sensor = EVNSmartmeterSensor(hass, entry)
    monthly_sensor = EVNSmartmeterMonthlySensor()

    hass.data.setdefault("evn_smartmeter_monthly_sensor", {})[
        entry.entry_id
    ] = monthly_sensor

    async_add_entities([consumption_sensor, monthly_sensor])

    # Store sensor reference so the reset service can call async_update
    hass.data[f"{DOMAIN}_sensor"] = consumption_sensor

    # Immediately fetch data on startup / reload
    hass.async_create_task(consumption_sensor.async_update())

    # Schedule daily fetch at 06:00
    async def scheduled_update(_now):
        await consumption_sensor.async_update()

    async_track_time_change(hass, scheduled_update, hour=6, minute=0, second=0)


class EVNSmartmeterSensor(SensorEntity):
    """Import sensor: fetches EVN data and saves to HA external statistics."""

    _attr_should_poll = False

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry_id = entry.entry_id
        self._username = entry.data[CONF_USERNAME]
        self._password = entry.data[CONF_PASSWORD]
        self._attr_name = "EVN Smart Meter Import"
        self._state = None
        self._api = None

    @property
    def state(self):
        return self._state

    async def _fetch_days(self, start, end):
        """Fetch consumption data for a date range (day by day)."""
        data = {}
        current = start
        while current <= end:
            try:
                values = await self._api.get_consumption_per_day(current)
                non_null = (
                    [v for v in values if v is not None] if values else []
                )
                if non_null:
                    data[current] = values
                    _LOGGER.debug(
                        "Day %s: %d non-null values, sum=%.3f kWh",
                        current.isoformat(),
                        len(non_null),
                        sum(non_null),
                    )
            except Exception:
                _LOGGER.debug("No data for %s", current.isoformat())
            current += timedelta(days=1)
        return data

    async def async_update(self):
        """Fetch EVN data and save to HA statistics.

        First import: walks backwards month by month until no data is found.
        Subsequent imports: fetches only new data since last known statistic.
        """
        try:
            self._api = Smartmeter(self._username, self._password)
            await self._api.authenticate()
            await self._api.get_meter_details()

            # Determine fetch range (elvia pattern)
            statistic_id = f"{DOMAIN}:consumption"
            recorder = get_instance(self.hass)

            force_reimport = self.hass.data.pop(
                f"{DOMAIN}_force_reimport", False
            )

            if force_reimport:
                last_stats = None
                _LOGGER.warning("Force reimport — ignoring existing stats")
            else:
                last_stats = await recorder.async_add_executor_job(
                    get_last_statistics, self.hass, 1, statistic_id, True, {"sum"},
                )

            today = date.today()
            yesterday = today - timedelta(days=1)

            if not last_stats:
                # First import: go back month by month until a full month
                # returns no data (= we've reached the beginning of history)
                _LOGGER.info("First import: fetching all available history")
                all_day_data = {}
                month_start = yesterday.replace(day=1)

                while True:
                    month_last = (
                        (month_start.replace(day=28) + timedelta(days=4))
                        .replace(day=1)
                        - timedelta(days=1)
                    )
                    month_end = min(month_last, yesterday)
                    month_data = await self._fetch_days(month_start, month_end)

                    if not month_data:
                        _LOGGER.info(
                            "No data for %s — history complete",
                            month_start.strftime("%Y-%m"),
                        )
                        break

                    all_day_data.update(month_data)
                    _LOGGER.info(
                        "Fetched %d days for %s",
                        len(month_data),
                        month_start.strftime("%Y-%m"),
                    )
                    # Previous month
                    month_start = (
                        month_start - timedelta(days=1)
                    ).replace(day=1)
            else:
                # Incremental: fetch from last known stat to yesterday
                last_end_ts = last_stats[statistic_id][0]["end"]
                start_date = dt_util.utc_from_timestamp(last_end_ts).date()
                _LOGGER.info(
                    "Incremental import: %d new days from %s",
                    (today - start_date).days,
                    start_date.isoformat(),
                )
                all_day_data = await self._fetch_days(start_date, yesterday)

            if all_day_data:
                await self.save_to_home_assistant(all_day_data, last_stats)
                self._update_monthly(all_day_data)
                self._state = "Imported"
                _LOGGER.warning(
                    "EVN import complete: %d days imported", len(all_day_data)
                )
            else:
                _LOGGER.warning("No consumption data found")
                self._state = "No data"

        except SmartmeterLoginError:
            _LOGGER.error("EVN login failed, check credentials")
            self._state = "Login error"
        except SmartmeterConnectionError as err:
            _LOGGER.warning("Connection error: %s", err)
            self._state = "Connection error"
        except Exception as err:
            _LOGGER.exception("Failed to update EVN data: %s", err)
            self._state = "Error"
        finally:
            if self._api:
                await self._api.close()

    async def save_to_home_assistant(self, all_data_by_date, last_stats):
        """Save consumption data as external statistics (elvia pattern).

        1. Use pre-fetched last_stats to determine cumulative sum baseline
        2. Aggregate 15-min EVN intervals into hourly buckets
        3. Build one list of StatisticData, call async_add_external_statistics once
        """
        statistic_id = f"{DOMAIN}:consumption"
        recorder = get_instance(self.hass)

        earliest_day = min(all_data_by_date.keys())
        window_start = dt_util.as_utc(
            datetime.combine(earliest_day, datetime.min.time())
        )

        if not last_stats:
            _sum = 0.0
            _LOGGER.debug("First import, starting sum at 0.0")
        else:
            # Find the cumulative sum just before our lookback window
            curr_stat = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass,
                window_start - timedelta(hours=1),
                window_start,
                {statistic_id},
                "hour",
                None,
                {"sum"},
            )
            if curr_stat and statistic_id in curr_stat and curr_stat[statistic_id]:
                _sum = cast(float, curr_stat[statistic_id][-1]["sum"])
                _LOGGER.debug("Resuming sum from %.3f kWh", _sum)
            else:
                _sum = 0.0
                _LOGGER.debug("No stats before window, starting sum at 0.0")

        # Build all statistics in one list
        statistics: list[StatisticData] = []
        for day_date, values in sorted(all_data_by_date.items()):
            # EVN provides 15-min intervals; HA requires hourly timestamps
            hourly_sums: dict[int, float] = {}
            for idx, value in enumerate(values):
                if value is not None:
                    hour = idx // 4
                    hourly_sums[hour] = hourly_sums.get(hour, 0.0) + value

            for hour in sorted(hourly_sums):
                _sum += hourly_sums[hour]
                ts = dt_util.as_utc(
                    datetime.combine(day_date, datetime.min.time())
                    + timedelta(hours=hour)
                )
                statistics.append(StatisticData(start=ts, sum=_sum))

        if statistics:
            try:
                async_add_external_statistics(
                    self.hass,
                    StatisticMetaData(
                        mean_type=StatisticMeanType.NONE,
                        has_sum=True,
                        name="EVN Smart Meter Consumption",
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

    def _update_monthly(self, all_data_by_date):
        """Update the monthly consumption sensor."""
        try:
            monthly_sensor = self.hass.data.get(
                "evn_smartmeter_monthly_sensor", {}
            ).get(self.entry_id)
            if not monthly_sensor:
                return

            today = date.today()
            total_kwh = sum(
                sum(v for v in values if v is not None)
                for day, values in all_data_by_date.items()
                if day.year == today.year and day.month == today.month
            )
            monthly_sensor.set_total(total_kwh)
            _LOGGER.info("Updated monthly sensor to %.3f kWh", total_kwh)
        except Exception as err:
            _LOGGER.warning("Failed to update monthly sensor: %s", err)


class EVNSmartmeterMonthlySensor(SensorEntity):
    """Monthly cumulative total sensor."""

    def __init__(self):
        self.entity_id = "sensor.evn_smartmeter_monthly_consumption"
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

