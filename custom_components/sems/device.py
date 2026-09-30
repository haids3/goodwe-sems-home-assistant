"""Device helpers for the SEMS integration."""

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

# HA replaced DeviceInfo's `via_device` identifier tuple with `via_device_id`,
# the registry id. Both forms are supported here because hacs.json still allows
# versions that predate the change.
_SUPPORTS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


# SEMS+ device names already describe the device ("Battery Rack 4", "Dongle 1"),
# so these labels are only prepended when the name does not carry them.
_DEVICE_TYPE_LABELS = {
    "INVERTER": "Inverter",
    "ENERGY_STORAGE_INTEGRATED_CABINET": "All-in-One",
    "BATTERY_RACK": "Battery Rack",
    "DONGLE": "Dongle",
}


def device_name_for_inverter(serial_number: str, inverter_data: dict[str, Any]) -> str:
    """Return a display name that matches what the device actually is."""
    name = str(inverter_data.get("name") or serial_number)

    device_type = inverter_data.get("deviceType")
    if not device_type:
        # Legacy SEMS payloads carry no type and only ever describe inverters.
        return f"Inverter {name}"

    label = _DEVICE_TYPE_LABELS.get(device_type)
    if not label:
        return name
    if label.casefold() in name.casefold():
        return name
    return f"{label} {name}"


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

    firmware_version = inverter_data.get("firmwareversion")
    if firmware_version in (None, ""):
        sw_version = "unknown"
    else:
        sw_version = str(firmware_version)

    # NOTE: We intentionally keep fallbacks here because not every SEMS payload
    # is guaranteed to contain `model_type`, `firmwareversion`, etc.
    device_info = DeviceInfo(
        identifiers={(DOMAIN, serial_number)},
        name=device_name_for_inverter(serial_number, inverter_data),
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
    if _SUPPORTS_VIA_DEVICE_ID:
        if station_device_id:
            device_info["via_device_id"] = station_device_id
    elif station_id := inverter_data.get("powerstation_id"):
        device_info["via_device"] = station_identifier(station_id)

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
