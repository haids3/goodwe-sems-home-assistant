"""Tests for SEMS device helpers."""

from unittest.mock import patch

from custom_components.sems.const import DOMAIN
from custom_components.sems.device import device_info_for_inverter


def test_device_info_sw_version_is_string_for_numeric_firmware() -> None:
    """Numeric firmware versions should be converted to strings."""
    device_info = device_info_for_inverter(
        "GW0000SN000TEST1",
        {"name": "Test Inverter", "model_type": "GW3000-NS", "firmwareversion": 1717.0},
    )

    assert device_info["sw_version"] == "1717.0"
    assert isinstance(device_info["sw_version"], str)


def test_device_info_sw_version_defaults_to_unknown_for_missing_firmware() -> None:
    """Missing firmware versions should use a string fallback."""
    device_info = device_info_for_inverter(
        "GW0000SN000TEST1",
        {"name": "Test Inverter", "model_type": "GW3000-NS"},
    )

    assert device_info["sw_version"] == "unknown"


def test_device_info_nests_under_station_by_device_id() -> None:
    """Current HA versions link the parent by its registry id."""
    with patch("custom_components.sems.device._SUPPORTS_VIA_DEVICE_ID", True):
        device_info = device_info_for_inverter(
            "GW0000SN000TEST1",
            {"name": "Test Inverter", "powerstation_id": "station-uuid"},
            "device-registry-id",
        )

    assert device_info["via_device_id"] == "device-registry-id"
    assert "via_device" not in device_info


def test_device_info_nests_under_station_by_identifier_on_older_ha() -> None:
    """Versions predating via_device_id still get the identifier form."""
    with patch("custom_components.sems.device._SUPPORTS_VIA_DEVICE_ID", False):
        device_info = device_info_for_inverter(
            "GW0000SN000TEST1",
            {"name": "Test Inverter", "powerstation_id": "station-uuid"},
            "device-registry-id",
        )

    assert device_info["via_device"] == (DOMAIN, "station-station-uuid")
    assert "via_device_id" not in device_info


def test_device_info_has_no_parent_without_a_station() -> None:
    """An inverter with no station id is left unparented."""
    device_info = device_info_for_inverter(
        "GW0000SN000TEST1", {"name": "Test Inverter"}, None
    )

    assert "via_device" not in device_info
    assert "via_device_id" not in device_info
