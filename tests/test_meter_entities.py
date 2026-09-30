"""Tests for smart meters as their own devices."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sems.const import CONF_STATION_ID, DOMAIN

MOCK_POWER_STATION_ID = "12345678-1234-5678-9abc-123456789abc"
MOCK_INVERTER_SN = "GW0000SN000TEST1"

# A station can have more than one meter; the old code only ever used the first.
MOCK_METERS = [
    {
        "sn": "METER1",
        "name": "Meter 1",
        "deviceType": "SMART_METER",
        "powerstation_id": MOCK_POWER_STATION_ID,
        "meter_power": 1351,
        "meter_phase_a_power": -0.451,
    },
    {
        "sn": "METER2",
        "name": "Meter 2",
        "deviceType": "SMART_METER",
        "powerstation_id": MOCK_POWER_STATION_ID,
        "meter_power": 42,
        "meter_phase_a_power": 0.1,
    },
]


def _get_data_result(meters: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a coordinator payload carrying the given meters."""
    return {
        "inverter": [
            {
                "invert_full": {
                    "name": "Test Inverter",
                    "sn": MOCK_INVERTER_SN,
                    "powerstation_id": MOCK_POWER_STATION_ID,
                    "deviceType": "INVERTER",
                    "status": 1,
                    "pac": 589,
                }
            }
        ],
        "kpi": {"currency": "EUR", "total_power": 1.0},
        "hasPowerflow": False,
        "hasEnergeStatisticsCharts": False,
        "smart_meters": meters,
    }


@contextmanager
def _mock_api(meters: list[dict[str, Any]]) -> Generator[None]:
    """Mock every API call the coordinator makes during setup."""
    with (
        patch(
            "custom_components.sems.sems_api.SemsApi.getData",
            return_value=_get_data_result(meters),
        ),
        patch(
            "custom_components.sems.sems_api.SemsApi.getEnergyStorageIntegratedCabinets",
            return_value=[],
        ),
        patch(
            "custom_components.sems.sems_api.SemsApi.getBatteryGeneralFunctions",
            return_value={},
        ),
        patch(
            "custom_components.sems.sems_api.SemsApi.getWebStationBasicInfo",
            return_value={"stationId": MOCK_POWER_STATION_ID, "name": "Test Station"},
        ),
        patch(
            "custom_components.sems.sems_api.SemsApi.getAlarmStatistics",
            return_value={},
        ),
    ):
        yield


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    """Add and set up a config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data={
            CONF_USERNAME: "user",
            CONF_PASSWORD: "pass",
            CONF_STATION_ID: MOCK_POWER_STATION_ID,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_each_meter_becomes_its_own_device(hass: HomeAssistant) -> None:
    """Both meters of a station get a device, nested under the station."""
    with _mock_api(MOCK_METERS):
        entry = await _setup(hass)

    dev_reg = dr.async_get(hass)
    station = dev_reg.async_get_device_by_identifier(
        (DOMAIN, f"station-{MOCK_POWER_STATION_ID}"), entry.entry_id
    )
    assert station is not None

    for serial, expected_name in (("METER1", "Meter 1"), ("METER2", "Meter 2")):
        device = dev_reg.async_get_device_by_identifier(
            (DOMAIN, serial), entry.entry_id
        )
        assert device is not None, serial
        # SEMS names meters "Meter N"; no "Smart Meter Meter 1".
        assert device.name == expected_name
        # SEMS reports no model for a meter.
        assert device.model == "Smart Meter"
        assert device.via_device_id == station.id


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_meter_entities_report_their_own_meters_values(
    hass: HomeAssistant,
) -> None:
    """Each meter's entities read that meter, not the first one."""
    with _mock_api(MOCK_METERS):
        entry = await _setup(hass)

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    for serial, expected in (("METER1", "1351"), ("METER2", "42")):
        entity_id = ent_reg.async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{serial}-meter_power"
        )
        assert entity_id is not None, serial

        registry_entry = ent_reg.async_get(entity_id)
        assert registry_entry is not None
        device = dev_reg.async_get_device_by_identifier(
            (DOMAIN, serial), entry.entry_id
        )
        assert device is not None
        assert registry_entry.device_id == device.id

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == expected


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_station_without_a_meter_gets_no_meter_device(
    hass: HomeAssistant,
) -> None:
    """A meter-less station must not grow an empty meter device."""
    with _mock_api([]):
        entry = await _setup(hass)

    dev_reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
    assert not [d for d in devices if d.name and "Smart Meter" in d.name]
