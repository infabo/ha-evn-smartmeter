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

LOOKBACK_DAYS = 7


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up EVN Smart Meter sensors from a config entry."""
    consumption_sensor = EVNSmartmeterSensor(hass, entry)
    monthly_sensor = EVNSmartmeterMonthlySensor()

    hass.data.setdefault("evn_smartmeter_monthly_sensor", {})[
        entry.entry_id
    ] = monthly_sensor

    async_add_entities([consumption_sensor, monthly_sensor])

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

    async def async_update(self):
        _LOGGER.debug("Starting EVN data fetch (lookback %d days)", LOOKBACK_DAYS)
        try:
            self._api = Smartmeter(self._username, self._password)
            await self._api.authenticate()
            await self._api.get_meter_details()

            today = date.today()
            all_day_data = {}

            for i in range(LOOKBACK_DAYS, 0, -1):
                day = today - timedelta(days=i)
                try:
                    values = await self._api.get_consumption_per_day(day)
                    non_null = (
                        [v for v in values if v is not None] if values else []
                    )
                    if non_null:
                        all_day_data[day] = values
                        _LOGGER.debug(
                            "Day %s: %d non-null values, sum=%.3f kWh",
                            day.isoformat(),
                            len(non_null),
                            sum(non_null),
                        )
                except Exception:
                    _LOGGER.debug("No data for %s", day.isoformat())

            if all_day_data:
                await self.save_to_home_assistant(all_day_data)
                self._update_monthly(all_day_data)
                self._state = "Imported"
                _LOGGER.warning(
                    "EVN import complete: %d days imported", len(all_day_data)
                )
            else:
                _LOGGER.warning("No consumption data found in lookback window")
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

    async def save_to_home_assistant(self, all_data_by_date):
        """Save consumption data as external statistics (elvia pattern).

        1. Check for existing statistics to determine cumulative sum baseline
        2. Aggregate 15-min EVN intervals into hourly buckets
        3. Build one list of StatisticData, call async_add_external_statistics once
        """
        statistic_id = f"{DOMAIN}:consumption"
        recorder = get_instance(self.hass)

        # Determine cumulative sum baseline (elvia pattern)
        last_stats = await recorder.async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"},
        )

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

