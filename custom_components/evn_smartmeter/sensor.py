"""Sensor platform for EVN Smart Meter integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EVNSmartmeterCoordinator

_LOGGER = logging.getLogger(__name__)

SENSOR_TOTAL = SensorEntityDescription(
    key="total_consumption",
    translation_key="total_consumption",
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    suggested_display_precision=2,
)

SENSOR_DAILY = SensorEntityDescription(
    key="daily_consumption",
    translation_key="daily_consumption",
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    suggested_display_precision=2,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVN Smart Meter sensors from a config entry."""
    coordinator: EVNSmartmeterCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            EVNSmartmeterTotalSensor(coordinator, entry),
            EVNSmartmeterDailySensor(coordinator, entry),
        ]
    )


class EVNSmartmeterBaseSensor(
    CoordinatorEntity[EVNSmartmeterCoordinator], SensorEntity
):
    """Base class for EVN Smart Meter sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EVNSmartmeterCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="EVN Smart Meter",
            manufacturer="Netz Niederösterreich",
            entry_type=DeviceEntryType.SERVICE,
        )


class EVNSmartmeterTotalSensor(EVNSmartmeterBaseSensor, RestoreEntity):
    """Sensor for total accumulated energy consumption (Energy Dashboard)."""

    def __init__(
        self,
        coordinator: EVNSmartmeterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the total consumption sensor."""
        super().__init__(coordinator, entry, SENSOR_TOTAL)

    async def async_added_to_hass(self) -> None:
        """Restore last known state on startup."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state not in (None, "unknown", "unavailable"):
            try:
                total = float(state.state)
                last_date = state.attributes.get("last_completed_date")
                current_day = float(
                    state.attributes.get("current_day_total", 0.0)
                )
                self.coordinator.restore_state(total, last_date, current_day)
                _LOGGER.debug(
                    "Restored total consumption: %.3f kWh (last_date=%s)",
                    total,
                    last_date,
                )
            except (ValueError, TypeError) as err:
                _LOGGER.warning("Could not restore previous state: %s", err)

    @property
    def native_value(self) -> float | None:
        """Return the total accumulated consumption."""
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.total_consumption, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes for persistence."""
        return {
            "last_completed_date": self.coordinator.last_completed_date_str,
            "current_day_total": round(self.coordinator.daily_consumption, 3),
            "meter_id": self.coordinator.meter_id,
        }


class EVNSmartmeterDailySensor(EVNSmartmeterBaseSensor):
    """Sensor for today's energy consumption so far."""

    def __init__(
        self,
        coordinator: EVNSmartmeterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the daily consumption sensor."""
        super().__init__(coordinator, entry, SENSOR_DAILY)

    @property
    def native_value(self) -> float | None:
        """Return today's consumption so far."""
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.daily_consumption, 3)

    @property
    def last_reset(self):
        """Return the time when the sensor was last reset (midnight)."""
        from datetime import datetime, time

        return datetime.combine(datetime.today(), time.min)
