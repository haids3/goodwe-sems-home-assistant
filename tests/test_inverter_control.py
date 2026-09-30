"""Tests for the Inverter Control switch driven by the device's control tree."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sems.const import CONF_STATION_ID, DOMAIN
from tests.fixtures import MOCK_GET_DATA_RESULT_MINIMAL

POWER_STATION_ID = "12345678-1234-5678-9abc-123456789abc"
INVERTER_SERIAL = "GW0000SN000TEST1"
RUN_STOP_ADDRESS = "45218"
RUN_STOP_ID = "1989877704267702274"


def _control_tree(run_stop_rw_type: str = "RW") -> dict[str, Any]:
    """Trimmed getTopTreeByCode response for an All-in-One."""
    return {
        "sn": INVERTER_SERIAL,
        "functionMenus": {
            "menuId": "1988896718675537921",
            "translateKey": "inverter",
            "children": [
                {
                    "menuId": "1988897383950872577",
                    "translateKey": "device_start_stop",
                    "children": [],
                    "functions": [
                        {
                            "address": RUN_STOP_ADDRESS,
                            "id": RUN_STOP_ID,
                            "rwType": run_stop_rw_type,
                            "translateKey": "run_stop",
                        },
                        {
                            "address": "45221",
                            "id": "2013543619255951363",
                            "rwType": "WO",
                            "translateKey": "restart",
                        },
                    ],
                }
            ],
        },
    }


async def _setup_entry(
    hass: HomeAssistant,
    *,
    tree: dict[str, Any],
    values: dict[str, Any],
) -> MagicMock:
    """Set up the entry and return the function-value request mock."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data={
            CONF_USERNAME: "user",
            CONF_PASSWORD: "pass",
            CONF_STATION_ID: POWER_STATION_ID,
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.sems.sems_api.SemsApi.getData",
            return_value={
                **MOCK_GET_DATA_RESULT_MINIMAL,
                "info": {"is_stored": False},
            },
        ),
        patch(
            "custom_components.sems.sems_api.SemsApi.getDeviceControlTree",
            return_value=tree,
        ),
        patch(
            "custom_components.sems.sems_api.SemsApi.getDeviceFunctionValues",
            return_value=values,
        ) as mock_values,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return mock_values


def _switch_entity_id(hass: HomeAssistant) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        Platform.SWITCH, DOMAIN, f"{INVERTER_SERIAL}-switch"
    )
    assert entity_id is not None
    return entity_id


@pytest.mark.parametrize(
    ("values", "expected_state"),
    [
        pytest.param({RUN_STOP_ADDRESS: 1}, STATE_ON, id="running"),
        # Status 1 ("Normal") would read on; the run/stop value must win.
        pytest.param({RUN_STOP_ADDRESS: 0}, STATE_OFF, id="stopped"),
        pytest.param({}, STATE_UNKNOWN, id="value-missing"),
    ],
)
async def test_state_follows_run_stop_value(
    hass: HomeAssistant, values: dict[str, Any], expected_state: str
) -> None:
    """The switch reports the run/stop function, not the production status."""
    await _setup_entry(hass, tree=_control_tree(), values=values)

    assert hass.states.get(_switch_entity_id(hass)).state == expected_state


@pytest.mark.parametrize(
    ("service", "running"),
    [
        pytest.param(SERVICE_TURN_ON, True, id="turn-on"),
        pytest.param(SERVICE_TURN_OFF, False, id="turn-off"),
    ],
)
async def test_commands_write_run_stop(
    hass: HomeAssistant, service: str, running: bool
) -> None:
    """Commands go to the run/stop function and never to the legacy path."""
    await _setup_entry(hass, tree=_control_tree(), values={RUN_STOP_ADDRESS: 1})

    with (
        patch(
            "custom_components.sems.sems_api.SemsApi.setInverterRunState"
        ) as mock_set,
        patch("custom_components.sems.sems_api.SemsApi.change_status") as mock_legacy,
    ):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            service,
            {ATTR_ENTITY_ID: _switch_entity_id(hass)},
            blocking=True,
        )

    mock_set.assert_called_once_with(
        POWER_STATION_ID,
        INVERTER_SERIAL,
        "Test Inverter",
        RUN_STOP_ADDRESS,
        RUN_STOP_ID,
        running,
    )
    mock_legacy.assert_not_called()


async def test_read_only_run_stop_falls_back_to_legacy(hass: HomeAssistant) -> None:
    """A run/stop function that cannot be written leaves the legacy path in place."""
    await _setup_entry(
        hass, tree=_control_tree(run_stop_rw_type="RO"), values={RUN_STOP_ADDRESS: 0}
    )
    entity_id = _switch_entity_id(hass)
    # Falls back to the production status, which is "Normal" in the fixture.
    assert hass.states.get(entity_id).state == STATE_ON

    with (
        patch(
            "custom_components.sems.sems_api.SemsApi.setInverterRunState"
        ) as mock_set,
        patch("custom_components.sems.sems_api.SemsApi.change_status") as mock_legacy,
    ):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    mock_set.assert_not_called()
    mock_legacy.assert_called_once_with(
        INVERTER_SERIAL, 2, POWER_STATION_ID, "Test Inverter"
    )


async def test_run_stop_values_are_not_read_without_the_function(
    hass: HomeAssistant,
) -> None:
    """A tree without run/stop costs no function-value request."""
    tree = deepcopy(_control_tree())
    tree["functionMenus"]["children"][0]["functions"].pop(0)

    mock_values = await _setup_entry(hass, tree=tree, values={})

    mock_values.assert_not_called()
