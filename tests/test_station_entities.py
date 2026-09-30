"""Tests for the station-wide SEMS entities: status, on/off-grid and alarms."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sems.const import CONF_STATION_ID, DOMAIN

MOCK_POWER_STATION_ID = "12345678-1234-5678-9abc-123456789abc"
MOCK_INVERTER_SN = "GW0000SN000TEST1"

MOCK_GET_DATA_RESULT = {
    "inverter": [
        {
            "invert_full": {
                "name": "Test Inverter",
                "sn": MOCK_INVERTER_SN,
                "powerstation_id": MOCK_POWER_STATION_ID,
                "status": 1,
                "capacity": 3.0,
                "pac": 589,
                "etotal": 18843.2,
            }
        }
    ],
    "kpi": {"currency": "EUR", "total_power": 18843.2},
    "hasPowerflow": False,
    "hasEnergeStatisticsCharts": False,
}

# Shape taken from a live app/v2/stations/basic/info response. SEMS sends these
# enums as strings.
MOCK_STATION_INFO = {
    "stationId": MOCK_POWER_STATION_ID,
    "name": "Test Station",
    "status": "1",
    "gridStatus": "1",
    "powerStationTypeUser": "HOUSEHOLD",
}

# Shape taken from a live v2/alarm/page response.
MOCK_ALARM_RECOVERED = {
    "warningNameEn": "Grid Waveform Abnormal",
    "warningNameZh": "电压波形检测异常",
    "cause": "E-G3-0-12_reason",
    "solution": "E-G3-0-12_solution",
    "deviceName": "All-in-One 1",
    "devicesn": MOCK_INVERTER_SN,
    "status": 1,
    "stationId": MOCK_POWER_STATION_ID,
    "stationname": "Test Station",
    "warningStationName": "Test Station",
    "deviceType": "Total_DeviceType_inverter",
    "faultClassification": ["Total_FaultType_Protect"],
    "duration": "1m 6s",
    "confirmed": False,
    "starStatus": False,
    "alarmLevel": "Total_FaultLevel_alarm",
    "warningid": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "warning_code": "E-G3-0-12",
    "error_code": "4096",
    "warningname": "E-G3-0-12_warning",
    "happentimes": "2026-09-07 03:58:51",
    "recoverytimes": "2026-09-07 03:59:57",
    "warninglevel": 0,
}

MOCK_ALARM_OCCURRING = {
    **MOCK_ALARM_RECOVERED,
    "warningid": "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
    "warningNameEn": "Utility Loss",
    "alarmLevel": "Total_FaultLevel_Fault",
    "warning_code": "E-G1-0-01",
    "status": 0,
    "recoverytimes": None,
    "duration": None,
}


@contextmanager
def _mock_api(
    station_info: dict[str, Any] | Exception | None = None,
    alarm_counts: dict[str, Any] | None = None,
    alarm_rows: list[dict[str, Any]] | None = None,
) -> Generator[dict[str, MagicMock]]:
    """Mock every API call the coordinator makes during setup."""
    station_kwargs: dict[str, Any] = (
        {"side_effect": station_info}
        if isinstance(station_info, Exception)
        else {"return_value": station_info if station_info is not None else {}}
    )
    with (
        patch(
            "custom_components.sems.sems_api.SemsApi.getData",
            return_value=MOCK_GET_DATA_RESULT,
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
            **station_kwargs,
        ) as station_mock,
        patch(
            "custom_components.sems.sems_api.SemsApi.getAlarmStatistics",
            return_value=alarm_counts if alarm_counts is not None else {},
        ) as counts_mock,
        patch(
            "custom_components.sems.sems_api.SemsApi.getAlarmPage",
            return_value={"dataList": alarm_rows or [], "total": len(alarm_rows or [])},
        ) as page_mock,
    ):
        yield {
            "station": station_mock,
            "counts": counts_mock,
            "page": page_mock,
        }


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


def _entity_id(hass: HomeAssistant, platform: Platform, key: str) -> str | None:
    """Return the entity ID of a station entity by its unique ID suffix."""
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"station-{MOCK_POWER_STATION_ID}-{key}"
    )


def _state(hass: HomeAssistant, platform: Platform, key: str) -> Any:
    """Return the state of a station entity by its unique ID suffix."""
    entity_id = _entity_id(hass, platform, key)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


@pytest.mark.parametrize(
    ("grid_status", "expected"),
    [
        pytest.param("1", "on", id="on_grid"),
        pytest.param("0", "off", id="off_grid"),
        pytest.param("7", "unknown", id="undocumented_value"),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_on_grid_state(
    hass: HomeAssistant, grid_status: str, expected: str
) -> None:
    """The on-grid sensor follows gridStatus and stays unknown when undocumented."""
    with _mock_api(station_info={**MOCK_STATION_INFO, "gridStatus": grid_status}):
        await _setup(hass)

    assert _state(hass, Platform.BINARY_SENSOR, "on_grid").state == expected


@pytest.mark.parametrize(
    ("status", "expected_status", "expected_online"),
    [
        pytest.param("1", "Running", "on", id="running"),
        pytest.param("0", "Offline", "off", id="offline"),
        pytest.param("2", "Fault", "on", id="fault"),
        pytest.param("3", "Waiting", "on", id="waiting"),
        pytest.param("11", "Constructing", "on", id="constructing"),
        pytest.param("4294967295", "Unknown", "on", id="unknown_sentinel"),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_station_status_and_online(
    hass: HomeAssistant, status: str, expected_status: str, expected_online: str
) -> None:
    """Station status maps to a label, and only status 0 means offline."""
    with _mock_api(station_info={**MOCK_STATION_INFO, "status": status}):
        await _setup(hass)

    assert _state(hass, Platform.SENSOR, "status").state == expected_status
    assert _state(hass, Platform.BINARY_SENSOR, "online").state == expected_online


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_station_entities_unavailable_without_station_info(
    hass: HomeAssistant,
) -> None:
    """Station entities report unavailable when basic/info returns nothing."""
    with _mock_api(station_info={}):
        await _setup(hass)

    assert _state(hass, Platform.BINARY_SENSOR, "online").state == "unavailable"
    assert _state(hass, Platform.SENSOR, "status").state == "unavailable"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_station_info_failure_keeps_inverter_entities(
    hass: HomeAssistant,
) -> None:
    """A basic/info failure must not take the inverter sensors down."""
    with _mock_api(station_info=TimeoutError("boom")):
        await _setup(hass)

    assert _state(hass, Platform.BINARY_SENSOR, "online").state == "unavailable"

    ent_reg = er.async_get(hass)
    power_entity_id = ent_reg.async_get_entity_id(
        Platform.SENSOR, DOMAIN, f"{MOCK_INVERTER_SN}-power"
    )
    assert power_entity_id is not None
    power_state = hass.states.get(power_entity_id)
    assert power_state is not None
    assert power_state.state == "589"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_active_alarms_and_attributes(hass: HomeAssistant) -> None:
    """The alarm count comes from `happened`, with the list in attributes."""
    with _mock_api(
        station_info=MOCK_STATION_INFO,
        alarm_counts={"total": "16", "happened": "2", "recovery": "14"},
        alarm_rows=[MOCK_ALARM_OCCURRING, MOCK_ALARM_RECOVERED],
    ):
        await _setup(hass)

    state = _state(hass, Platform.SENSOR, "active_alarms")
    assert state.state == "2"
    assert state.attributes["total"] == 16
    assert state.attributes["recovered"] == 14

    occurring, recovered = state.attributes["alarms"]
    assert occurring == {
        "id": "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
        "name": "Utility Loss",
        "code": "E-G1-0-01",
        "device": "All-in-One 1",
        "station": "Test Station",
        "level": "fault",
        "status": "occurring",
        "happened": "2026-09-07 03:58:51",
        "recovered": None,
        "duration": None,
        "confirmed": False,
    }
    # The two severities differ in case upstream; both normalize.
    assert recovered["level"] == "alarm"
    assert recovered["status"] == "recovered"
    # warningname is a translation key, so the English name must win.
    assert recovered["name"] == "Grid Waveform Abnormal"


@pytest.mark.parametrize(
    ("happened", "expected"),
    [
        pytest.param("2", "on", id="occurring"),
        pytest.param("0", "off", id="clear"),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_binary_sensor(
    hass: HomeAssistant, happened: str, expected: str
) -> None:
    """The problem sensor is on only while an alarm is occurring."""
    with _mock_api(
        station_info=MOCK_STATION_INFO,
        alarm_counts={"total": "16", "happened": happened, "recovery": "14"},
        alarm_rows=[MOCK_ALARM_RECOVERED],
    ):
        await _setup(hass)

    assert _state(hass, Platform.BINARY_SENSOR, "alarm").state == expected


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_entities_unavailable_without_counts(hass: HomeAssistant) -> None:
    """Alarm entities report unavailable when the counts call returns nothing."""
    with _mock_api(station_info=MOCK_STATION_INFO, alarm_counts={}):
        await _setup(hass)

    assert _state(hass, Platform.BINARY_SENSOR, "alarm").state == "unavailable"
    assert _state(hass, Platform.SENSOR, "active_alarms").state == "unavailable"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_list_skipped_while_station_is_clear(hass: HomeAssistant) -> None:
    """A station that never alarmed costs no list request."""
    with _mock_api(
        station_info=MOCK_STATION_INFO,
        alarm_counts={"total": "0", "happened": "0", "recovery": "0"},
    ) as mocks:
        await _setup(hass)

        assert mocks["page"].call_count == 0

    assert _state(hass, Platform.SENSOR, "active_alarms").attributes["alarms"] == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_recovered_alarm_list_is_throttled(hass: HomeAssistant) -> None:
    """History is re-read on its own interval, not on every refresh."""
    with _mock_api(
        station_info=MOCK_STATION_INFO,
        alarm_counts={"total": "16", "happened": "0", "recovery": "16"},
        alarm_rows=[MOCK_ALARM_RECOVERED],
    ) as mocks:
        entry = await _setup(hass)
        assert mocks["page"].call_count == 1

        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

        assert mocks["page"].call_count == 1
        # The cached list survives the refresh that skipped the request.
        assert (
            len(_state(hass, Platform.SENSOR, "active_alarms").attributes["alarms"])
            == 1
        )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_occurring_alarm_list_refreshes_every_update(hass: HomeAssistant) -> None:
    """An occurring alarm is worth a list request on every refresh."""
    with _mock_api(
        station_info=MOCK_STATION_INFO,
        alarm_counts={"total": "16", "happened": "1", "recovery": "15"},
        alarm_rows=[MOCK_ALARM_OCCURRING],
    ) as mocks:
        entry = await _setup(hass)
        assert mocks["page"].call_count == 1

        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

        assert mocks["page"].call_count == 2


@pytest.mark.parametrize(
    "station_info",
    [
        pytest.param({**MOCK_STATION_INFO, "gridStatus": None}, id="null_grid_status"),
        pytest.param({**MOCK_STATION_INFO, "gridStatus": ""}, id="empty_grid_status"),
        pytest.param(
            {k: v for k, v in MOCK_STATION_INFO.items() if k != "gridStatus"},
            id="absent_grid_status",
        ),
        pytest.param({}, id="no_station_info"),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_on_grid_not_created_without_grid_status(
    hass: HomeAssistant, station_info: dict[str, Any]
) -> None:
    """PV-only stations never report gridStatus, so they get no on-grid entity."""
    with _mock_api(station_info=station_info):
        await _setup(hass)

    assert _entity_id(hass, Platform.BINARY_SENSOR, "on_grid") is None
    # The rest of the station entities are unaffected.
    assert _entity_id(hass, Platform.BINARY_SENSOR, "online") is not None
    assert _entity_id(hass, Platform.SENSOR, "status") is not None
