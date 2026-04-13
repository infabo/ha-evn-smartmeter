"""Sensor platform for EVN Smart Meter integration.

Architecture matches enelgrid (github.com/sathia-musso/enelgrid) exactly:
- One import sensor that fetches data and saves to external statistics
- One monthly sensor showing cumulative kWh for the current month
- All 15-min consumption data lives in HA external statistics
"""

import logging
from datetime import date, datetime, time, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time
from homeassistant.util.dt import as_utc

from .const import DAILY_UPDATE_HOUR, DOMAIN
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

    _LOGGER.warning(
        "EVN Smart Meter sensors added: %s, %s",
        consumption_sensor.entity_id,
        monthly_sensor.entity_id,
    )

    # Immediately fetch data on startup
    hass.async_create_task(consumption_sensor.async_update())

    # Schedule daily fetch at DAILY_UPDATE_HOUR:00
    async def scheduled_update(_now):
        _LOGGER.debug("Daily scheduled update triggered")
        await consumption_sensor.async_update()

    async_track_time(hass, scheduled_update, time(DAILY_UPDATE_HOUR, 0, 0))


class EVNSmartmeterSensor(SensorEntity):
    """Import sensor: fetches EVN data and saves to HA external statistics."""

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
                except Exception:
                    _LOGGER.debug("No data for %s", day.isoformat())

            if all_day_data:
                await self.save_to_home_assistant(all_day_data)
                self._update_monthly(all_day_data)
                self._state = "Imported"
            else:
                _LOGGER.warning("No consumption data found in lookback window")
                self._state = "No data"

        except SmartmeterLoginError:
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
        """Save consumption data to HA external statistics.

        Matches enelgrid save_to_home_assistant exactly.
        """
        statistic_id = "sensor:evn_smartmeter_consumption"

        metadata = {
            "has_mean": False,
            "has_sum": True,
            "name": "EVN Smart Meter Consumption",
            "source": "sensor",
            "statistic_id": statistic_id,
            "unit_of_measurement": "kWh",
        }

        cumulative_offset = await self.get_last_cumulative_kwh(statistic_id)

        running_sum = 0.0
        for day_date, values in sorted(all_data_by_date.items()):
            stats = []
            for idx, value in enumerate(values):
                if value is not None:
                    running_sum += value
                    final_value = running_sum + cumulative_offset
                    ts = datetime.combine(
                        day_date, datetime.min.time()
                    ) + timedelta(minutes=idx * 15)
                    stats.append(
                        {
                            "start": as_utc(ts),
                            "sum": final_value,
                        }
                    )

            if stats:
                async_add_external_statistics(self.hass, metadata, stats)
                _LOGGER.info(
                    "Saved %d points for %s", len(stats), day_date.isoformat()
                )

    async def get_last_cumulative_kwh(self, statistic_id):
        """Get the last recorded cumulative kWh for a statistic."""
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )
        if last_stats and statistic_id in last_stats:
            return last_stats[statistic_id][0]["sum"]
        return 0.0

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
