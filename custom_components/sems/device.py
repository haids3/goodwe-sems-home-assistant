"""Device helpers for the SEMS integration."""

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def station_identifier(station_id: str) -> tuple[str, str]:
    """Return the device registry identifier of a power station."""
    return (DOMAIN, f"station-{station_id}")


def device_info_for_inverter(
    serial_number: str,
    inverter_data: dict[str, Any],
    station_device_id: str | None = None,
) -> DeviceInfo:
    """Build device info for an inverter.

    This is shared across platforms (sensor, switch, etc.) so entities for the
    same inverter are grouped under the same device and show a consistent name.
    """

    name = inverter_data.get("name") or serial_number

    firmware_version = inverter_data.get("firmwareversion")
    if firmware_version in (None, ""):
        sw_version = "unknown"
    else:
        sw_version = str(firmware_version)

    # NOTE: We intentionally keep fallbacks here because not every SEMS payload
    # is guaranteed to contain `model_type`, `firmwareversion`, etc.
    device_info = DeviceInfo(
        identifiers={(DOMAIN, serial_number)},
        name=f"Inverter {name}",
        manufacturer="GoodWe",
        model=inverter_data.get("model_type", "unknown"),
        sw_version=sw_version,
        configuration_url=(
            f"https://semsportal.com/PowerStation/PowerStatusSnMin/"
            f"{inverter_data.get('powerstation_id')}"
            if inverter_data.get("powerstation_id")
            else None
        ),
    )

    # Nest the inverter under its station so a single-inverter system does not
    # show two unrelated top-level devices.
    if station_device_id:
        device_info["via_device_id"] = station_device_id

    return device_info


def device_info_for_station(
    station_id: str, station_info: dict[str, Any] | None
) -> DeviceInfo:
    """Build device info for the power station itself.

    Station-wide entities (status, on/off-grid, alarms) group here rather than
    under any single inverter.
    """

    info = station_info or {}
    name = info.get("name") or station_id

    return DeviceInfo(
        identifiers={station_identifier(station_id)},
        name=f"Station {name}",
        manufacturer="GoodWe",
        model=info.get("powerStationTypeUser") or "unknown",
        configuration_url=(
            f"https://semsportal.com/PowerStation/PowerStatusSnMin/{station_id}"
        ),
    )
