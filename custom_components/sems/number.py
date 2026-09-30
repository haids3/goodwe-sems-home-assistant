"""Number platform for the GoodWe SEMS integration."""

from collections.abc import Callable

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SemsConfigEntry, SemsCoordinator
from .const import CONF_STATION_ID
from .device import device_info_for_inverter

type SetBatteryValueMethod = Callable[[str, str, str, int, str, str], None]


class SemsBatteryNumber(CoordinatorEntity[SemsCoordinator], NumberEntity):
    """Number controlling a battery immediate-charging setting."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SemsCoordinator,
        plant_id: str,
        serial_number: str,
        battery_id: str,
        battery_name: str,
        function: dict[str, str],
        function_name: str,
        name: str,
        value_key: str,
        method: SetBatteryValueMethod,
    ) -> None:
        super().__init__(coordinator)
        inverter_data = coordinator.data.inverters.get(serial_number, {})
        self._attr_device_info = device_info_for_inverter(
            serial_number, inverter_data, coordinator.station_device_id
        )
        self._attr_unique_id = f"{serial_number}-{battery_id}-{function_name}"
        self._attr_name = f"Battery {battery_name} {name}"
        self.plant_id = plant_id
        self.serial_number = serial_number
        self.battery_id = battery_id
        self.function = function
        self.value_key = value_key
        self.method = method

    @property
    def native_value(self) -> float:
        return float(
            (self.coordinator.data.immediate_charging or {})
            .get(self.serial_number, {})
            .get(self.value_key, 0.0)
        )

    async def async_set_native_value(self, value: float) -> None:
        await self._async_set_value(self.method, value)

    async def _async_set_value(
        self, method: SetBatteryValueMethod, value: float
    ) -> None:
        if self.coordinator.data is None:
            raise HomeAssistantError(
                f"Unable to set value for {self.entity_id}: no coordinator data"
            )

        await self.hass.async_add_executor_job(
            method,
            self.plant_id,
            self.serial_number,
            self.battery_id,
            int(value),
            self.function["address"],
            self.function["id"],
        )
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SemsConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up SEMS number entities from a config entry."""
    coordinator: SemsCoordinator = config_entry.runtime_data.coordinator
    known_unique_ids: set[str] = set()

    @callback
    def async_add_new_numbers() -> None:
        """Add number entities for batteries that were not in the payload yet.

        A request that failed or was rate limited during setup would otherwise
        leave them missing until the entry was reloaded.
        """
        new_entities = [
            entity
            for entity in _build_numbers(coordinator, config_entry)
            if entity.unique_id not in known_unique_ids
        ]
        if not new_entities:
            return

        known_unique_ids.update(str(entity.unique_id) for entity in new_entities)
        async_add_entities(new_entities)

    async_add_new_numbers()
    config_entry.async_on_unload(coordinator.async_add_listener(async_add_new_numbers))


def _build_numbers(
    coordinator: SemsCoordinator, config_entry: SemsConfigEntry
) -> list[NumberEntity]:
    """Build every number entity the current coordinator data supports."""
    number_entities: list[NumberEntity] = []

    for sn, bats in (coordinator.data.batteries or {}).items():
        for bat_id, bat_data in bats.items():
            functions = bat_data["functions"]
            for function_name, name, value_key, method in (
                (
                    "end_charge_soc",
                    "End Charge SoC",
                    "end_charge_soc",
                    coordinator.sems_api.setImmediateChargingEndSoC,
                ),
                (
                    "bat_immediate_charge_power",
                    "Charging Power",
                    "charging_power",
                    coordinator.sems_api.setImmediateChargingChargingPower,
                ),
            ):
                if function := functions.get(function_name):
                    number_entities.append(
                        SemsBatteryNumber(
                            coordinator,
                            config_entry.data[CONF_STATION_ID],
                            sn,
                            bat_id,
                            bat_data["name"],
                            function,
                            function_name,
                            name,
                            value_key,
                            method,
                        )
                    )

    return number_entities
