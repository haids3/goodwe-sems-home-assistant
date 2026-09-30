"""Support for station status and alarms from the GoodWe SEMS API.

For more details about this platform, please refer to the documentation at
https://github.com/TimSoethout/goodwe-sems-home-assistant
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SemsCoordinator
from .const import (
    CONF_STATION_ID,
    STATION_OFF_GRID,
    STATION_ON_GRID,
    coerce_api_int,
)
from .device import device_info_for_station

_LOGGER = logging.getLogger(__name__)

_STATION_STATUS_OFFLINE = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SEMS binary sensors from a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    station_id = config_entry.data[CONF_STATION_ID]

    entities: list[BinarySensorEntity] = [
        SemsStationOnlineBinarySensor(coordinator, station_id),
        SemsAlarmBinarySensor(coordinator, station_id),
    ]

    # Only battery stations report gridStatus; islanding is meaningless without
    # one, and PV-only stations omit the field entirely.
    if (coordinator.data.station_info or {}).get("gridStatus") not in (None, ""):
        entities.append(SemsOnGridBinarySensor(coordinator, station_id))

    async_add_entities(entities)


class SemsStationBinarySensorBase(
    CoordinatorEntity[SemsCoordinator], BinarySensorEntity
):
    """Base for binary sensors describing the station as a whole."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SemsCoordinator,
        station_id: str,
        key: str,
        name: str,
    ) -> None:
        """Initialize the station binary sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_device_info = device_info_for_station(
            station_id, coordinator.data.station_info
        )
        self._attr_unique_id = f"station-{station_id}-{key}"
        self._attr_name = name


class SemsStationInfoBinarySensor(SemsStationBinarySensorBase):
    """Base for binary sensors backed by the station basic-info call."""

    @property
    def available(self) -> bool:
        """Return whether station info was included in the last refresh."""
        return super().available and self.coordinator.data.station_info is not None

    def _station_field(self, key: str) -> int | None:
        """Return a station-info enum field as an int."""
        return coerce_api_int((self.coordinator.data.station_info or {}).get(key))


class SemsOnGridBinarySensor(SemsStationInfoBinarySensor):
    """Whether the station is connected to the grid."""

    def __init__(self, coordinator: SemsCoordinator, station_id: str) -> None:
        """Initialize the on-grid sensor."""
        super().__init__(coordinator, station_id, "on_grid", "On Grid")

    @property
    def is_on(self) -> bool | None:
        """Return True when on-grid, False when off-grid."""
        grid_status = self._station_field("gridStatus")
        if grid_status == STATION_ON_GRID:
            return True
        if grid_status == STATION_OFF_GRID:
            return False
        # Any other value is undocumented; report unknown rather than guessing.
        return None


class SemsStationOnlineBinarySensor(SemsStationInfoBinarySensor):
    """Whether the station is reporting to SEMS."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: SemsCoordinator, station_id: str) -> None:
        """Initialize the station connectivity sensor."""
        super().__init__(coordinator, station_id, "online", "Online")

    @property
    def is_on(self) -> bool | None:
        """Return True when the station is not reporting as offline."""
        status = self._station_field("status")
        if status is None:
            return None
        return status != _STATION_STATUS_OFFLINE


class SemsAlarmBinarySensor(SemsStationBinarySensorBase):
    """Whether the station currently has an alarm."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SemsCoordinator, station_id: str) -> None:
        """Initialize the alarm sensor."""
        super().__init__(coordinator, station_id, "alarm", "Alarm")

    @property
    def available(self) -> bool:
        """Return whether alarm counts were included in the last refresh."""
        return super().available and self.coordinator.data.alarm_counts is not None

    @property
    def is_on(self) -> bool | None:
        """Return True while at least one alarm is occurring."""
        counts = self.coordinator.data.alarm_counts or {}
        happened = coerce_api_int(counts.get("happened"))
        if happened is None:
            return None
        return happened > 0
