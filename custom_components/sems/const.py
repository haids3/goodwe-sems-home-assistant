"""Constants for the SEMS integration."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

# import voluptuous as vol
# from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME

DOMAIN = "sems"

PLATFORMS = ["binary_sensor", "number", "sensor", "switch"]

CONF_STATION_ID = "powerstation_id"

# SEMS reports these through the same device list as inverters, but a battery
# rack only reports its own telemetry and charge counters and a dongle reports
# nothing but its status. Applying the inverter template to them created dozens
# of entities per device that never receive data.
NON_INVERTER_DEVICE_TYPES = frozenset({"BATTERY_RACK", "DONGLE"})

# GoodWe "Power Meter" installations report no serial. This constant stood in for
# one, which made every meter-less station of an account share a unique-ID
# namespace; it is now only a sentinel, normalised per station in the sensor
# platform.
HOMEKIT_NO_SERIAL = "GW-HOMEKIT-NO-SERIAL"

DEFAULT_SCAN_INTERVAL = 60  # timedelta(seconds=60)


def account_key(username: str) -> str:
    """Return the key that identifies a SEMS account."""
    return username.strip().casefold()


AC_EMPTY = 6553.5
AC_CURRENT_EMPTY = 6553.5
AC_FEQ_EMPTY = 655.35


STATUS_LABELS = {
    -1: "Offline",
    0: "Waiting",
    1: "Normal",
    2: "Fault",
    3: "Waiting",
    5: "Normal",
}
GRID_STATUS_LABELS = {-1: "Offline", 0: "Waiting", 1: "Normal", 2: "Fault"}
INVERTER_ON_STATUSES = frozenset(
    status for status, label in STATUS_LABELS.items() if label == "Normal"
)


# Station-wide status from app/v2/stations/basic/info. This is a different scale
# from STATUS_LABELS, which describes a single inverter.
STATION_STATUS_LABELS = {
    0: "Offline",
    1: "Running",
    2: "Fault",
    3: "Waiting",
    11: "Constructing",
}
STATION_STATUS_UNKNOWN = "Unknown"

# basic/info gridStatus. Unrelated to GRID_STATUS_LABELS, which encodes the
# import/export sign of the HomeKit power flow.
STATION_ON_GRID = 1
STATION_OFF_GRID = 0

# Alarm severity arrives as "Total_FaultLevel_alarm"/"Total_FaultLevel_Fault".
ALARM_LEVEL_PREFIX = "Total_FaultLevel_"

# AlarmStatusEnum from the SEMS+ app.
ALARM_STATUS_LABELS = {
    0: "occurring",
    1: "recovered",
}


def coerce_api_int(value: Any) -> int | None:
    """Return a SEMS enum value as an int.

    SEMS returns these enums as strings ("1"), so callers cannot index the
    label maps directly.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except ValueError:
        return None


class GOODWE_SPELLING:
    """Constants for correcting GoodWe API spelling errors."""

    battery = "bettery"
    batteryStatus = "betteryStatus"
    homeKit = "homKit"
    temperature = "tempperature"
    hasEnergyStatisticsCharts = "hasEnergeStatisticsCharts"
    energyStatisticsCharts = "energeStatisticsCharts"
    energyStatisticsTotals = "energeStatisticsTotals"
    thisMonthTotalE = "thismonthetotle"
    lastMonthTotalE = "lastmonthetotle"


def redact_value(value: str) -> str:
    """Return a partial redaction of a sensitive value for logging.

    Shows enough of the value to remain unique/recognizable while hiding
    the sensitive information.
    """
    if not value:
        return "<redacted>"

    # For email addresses, show domain but redact local part
    if "@" in value:
        parts = value.rsplit("@", 1)
        return f"<***@{parts[1]}>"

    # For UUIDs (8-4-4-4-12 pattern with hyphens)
    if value.count("-") == 4 and len(value) == 36:
        return f"<{value[:4]}...{value[-4:]}>"

    # For longer strings (SNs, IDs), show first and last few chars
    if len(value) > 8:
        return f"<{value[:3]}...{value[-3:]}>"

    # For short strings, just show pattern
    return f"<{value[0]}{'*' * (len(value) - 1)}>"


_SENSITIVE_LOG_KEYS = {
    "account",
    "pwd",
    "password",
    "token",
    "uid",
    "sn",
    "serialnum",
    "relationid",
    "relation_id",
    "pw_id",
    "powerstation_id",
    "owner_email",
    "owner_name",
    "owner_phone",
}

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SERIAL_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{12,20}$")


def _matches_sensitive_pattern(value: str) -> bool:
    """Return whether a string looks sensitive by format."""
    return bool(
        _EMAIL_PATTERN.fullmatch(value)
        or _UUID_PATTERN.fullmatch(value)
        or _SERIAL_PATTERN.fullmatch(value)
    )


def _is_sensitive_label(key: Any) -> bool:
    """Return whether a dictionary key implies its value is sensitive."""
    return isinstance(key, str) and key.lower() in _SENSITIVE_LOG_KEYS


def _redact_sensitive_value(value: Any) -> Any:
    """Redact a value associated with a sensitive key."""
    if isinstance(value, str):
        return redact_value(value)

    if isinstance(value, dict):
        return {
            key: _redact_sensitive_value(sub_value) for key, sub_value in value.items()
        }

    if isinstance(value, list):
        return [_redact_sensitive_value(item) for item in value]

    # For non-string, non-container types (numbers, booleans, None, etc.), keep as-is
    return value


def redact_for_log(value: Any) -> Any:
    """Return a redacted structure suitable for debug logging."""
    if isinstance(value, str):
        return redact_value(value) if _matches_sensitive_pattern(value) else value

    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, sub_value in value.items():
            if isinstance(key, str) and _matches_sensitive_pattern(key):
                sanitized[redact_value(key)] = _redact_sensitive_value(sub_value)
            elif _is_sensitive_label(key):
                sanitized[key] = _redact_sensitive_value(sub_value)
            else:
                sanitized[key] = redact_for_log(sub_value)
        return sanitized

    if isinstance(value, list):
        return [redact_for_log(item) for item in value]

    # Handle dataclass instances by converting to dict and recursing
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return redact_for_log(dataclasses.asdict(value))

    return value
