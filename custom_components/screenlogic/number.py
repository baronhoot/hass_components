"""Support for a ScreenLogic number entity."""
import logging
import struct

from screenlogicpy.const import BODY_TYPE, DATA as SL_DATA, EQUIPMENT, SCG, CODE

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ScreenlogicEntity
from .const import DOMAIN

from .api.protocol import ScreenLogicProtocol
from .api.request import async_make_request

SETPUMPFLOW_QUERY = 12586
SETPUMPFLOW_ANSWER = SETPUMPFLOW_QUERY + 1

async def async_request_set_pump_flow(
    protocol: ScreenLogicProtocol, body: int, pumpID: int, flow: int, isRPMs: bool
) -> bool:
    return (
        await async_make_request(
            protocol, SETPUMPFLOW_QUERY, struct.pack("<IIIII", 0, pumpID, body, flow, isRPMs)
        )
        == b""
    )

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

SUPPORTED_SCG_NUMBERS = (
    "scg_level1",
    "scg_level2",
)

PUMP_TYPE_UNITS = {
    1: "gpm",
    2: "rpm"
}

PUMP_DATA_TYPE_KEY = "pumpType"
PUMP_DATA_TYPE_SETTING_KEYS = {
    1: "currentGPM",
    2: "currentRPM"
}
PUMP_DATA_SETTING_VALUE_KEY = "value"
PUMP_DATA_NAME_KEY = "name"
PUMP_DATA_STATE_KEY = "state"
PUMP_DATA_CURRENT_WATTS_KEY = "currentWatts"
PUMP_DATA_PRESETS_KEY = "presets"

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Chlorinators
    equipment_flags = coordinator.data[SL_DATA.KEY_CONFIG]["equipment_flags"]
    if equipment_flags & EQUIPMENT.FLAG_CHLORINATOR:
        async_add_entities(
            [
                ScreenLogicNumber(coordinator, scg_level)
                for scg_level in coordinator.data[SL_DATA.KEY_SCG]
                if scg_level in SUPPORTED_SCG_NUMBERS
            ]
        )

    # Pumps
    for pump_num, pump_data in coordinator.data[SL_DATA.KEY_PUMPS].items():
        if pump_data["data"] != 0 and pump_data[PUMP_DATA_TYPE_KEY] != 0:
            for preset_id, preset_data in pump_data["presets"].items():
                if preset_data["cid"] == 0:
                    continue

                flow_type = "rpm" if preset_data["isRPM"] == 1 else "gpm"
                async_add_entities(
                    [ScreenLogicPumpPreset(coordinator, pump_num, flow_type, preset_id, preset_data)]
                    )


class ScreenLogicPumpPreset(ScreenlogicEntity, NumberEntity):
    """Class to represent a ScreenLogic Variable Speed Pump Preset."""

    def __init__(self, coordinator, pump_id, flow_type, preset_id, preset_data):
        """Initialize of the entity."""

        super().__init__(coordinator, f"pump_{pump_id}_preset_{preset_id}_{flow_type}", True)

        pump_data = coordinator.data[SL_DATA.KEY_PUMPS][pump_id]
        self._pump_id = pump_id
        self._attr_mode = NumberMode.SLIDER
        self._flow_type = flow_type
        self._preset_id = preset_id

        device_name_mapping = {
            1: "Spa",
            6: "Pool",
            129: "Heater",
            132: "Freeze Protect"
        }

        device_id = preset_data["cid"]
        preset_name = device_name_mapping[device_id] if device_id in device_name_mapping else "Unknown"
        full_preset_name = f"{self.gateway_name} {pump_data[PUMP_DATA_NAME_KEY]} {preset_name} Preset"

        if flow_type == "gpm":
            self._attr_native_min_value = 20
            self._attr_native_max_value = 90
            self._attr_native_step = 1
            self._attr_name = f"{full_preset_name} (GPM)"
            self._attr_native_unit_of_measurement = "gal/min"
            self.isRPMs = False
        else:
            self._attr_native_min_value = 1000
            self._attr_native_max_value = 3450
            self._attr_native_step = 10
            self._attr_name = f"{full_preset_name} (RPM)"
            self._attr_native_unit_of_measurement = "rpm"
            self.isRPMs = True

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return float(self.pump_data["presets"][self._preset_id]["setPoint"])

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        _LOGGER.debug(f"PumpFlow SET: {int(value)} vs {self.native_value}")
        await self.async_set_preset(self._preset_id, self._pump_id, int(value), self.isRPMs)
        # Update pumps
        await self._async_refresh()

    async def async_set_preset(self, preset_id: str, pumpID: int, flow: int, isRPMs: bool):
        """Sets the pump flow rpm for the specified preset."""
        preset = int(preset_id)
        if not self._is_valid_preset(preset):
            raise ValueError(f"Invalid preset: {preset}")
        if not self._is_valid_pumpflow(flow, isRPMs):
            raise ValueError(f"Invalid flow ({flow}) for preset ({preset}) isRPMs={isRPMs}")

        if await self.coordinator.gateway.async_connect():
            if await async_request_set_pump_flow(
                self.coordinator.gateway._ScreenLogicGateway__protocol, preset, pumpID, flow, isRPMs):
                return True
        return False

    def _is_valid_pumpflow(self, flow, isRPMs):
        if isRPMs:
            min_flow = 1000
            max_flow = 3450
        else:
            min_flow = 20
            max_flow = 90
        return min_flow <= flow <= max_flow

    def _is_valid_preset(self, preset):
        presets = self.pump_data["presets"]
        if preset not in presets:
            return False
        return presets[preset]["cid"] != 0

    @property
    def pump_data(self) -> dict:
        return self.coordinator.data[SL_DATA.KEY_PUMPS][self._pump_id]

    @property
    def preset_data(self) -> dict:
        presets = self.pump_data["presets"]
        if self._preset_id not in presets:
            return None
        return presets[self._preset_id]

    @property
    def pump_type(self) -> dict:
        return self.pump_data[PUMP_DATA_TYPE_KEY]


class ScreenLogicNumber(ScreenlogicEntity, NumberEntity):
    """Class to represent a ScreenLogic Number."""

    def __init__(self, coordinator, data_key, enabled=True):
        """Initialize of the entity."""
        super().__init__(coordinator, data_key, enabled)
        self._body_type = SUPPORTED_SCG_NUMBERS.index(self._data_key)
        self._attr_native_max_value = SCG.LIMIT_FOR_BODY[self._body_type]
        self._attr_name = f"{self.gateway_name} {self.sensor['name']}"
        self._attr_native_unit_of_measurement = self.sensor["unit"]

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self.sensor["value"]

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        # Need to set both levels at the same time, so we gather
        # both existing level values and override the one that changed.
        levels = {}
        for level in SUPPORTED_SCG_NUMBERS:
            levels[level] = self.coordinator.data[SL_DATA.KEY_SCG][level]["value"]
        levels[self._data_key] = int(value)

        if await self.coordinator.gateway.async_set_scg_config(
            levels[SUPPORTED_SCG_NUMBERS[BODY_TYPE.POOL]],
            levels[SUPPORTED_SCG_NUMBERS[BODY_TYPE.SPA]],
        ):
            _LOGGER.debug(
                "Set SCG to %i, %i",
                levels[SUPPORTED_SCG_NUMBERS[BODY_TYPE.POOL]],
                levels[SUPPORTED_SCG_NUMBERS[BODY_TYPE.SPA]],
            )
            await self._async_refresh()
        else:
            _LOGGER.warning(
                "Failed to set_scg to %i, %i",
                levels[SUPPORTED_SCG_NUMBERS[BODY_TYPE.POOL]],
                levels[SUPPORTED_SCG_NUMBERS[BODY_TYPE.SPA]],
            )

    @property
    def sensor(self) -> dict:
        """Shortcut to access the level sensor data."""
        return self.coordinator.data[SL_DATA.KEY_SCG][self._data_key]
