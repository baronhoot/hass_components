"""Support for Lutron Caseta Occupancy/Vacancy Sensors and Keypad LEDs."""
from pylutron_caseta import OCCUPANCY_GROUP_OCCUPIED
from pylutron_caseta.smartbridge import Smartbridge

from typing import Any, Dict
import logging
from collections.abc import Iterable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.const import ATTR_MODEL, ATTR_MANUFACTURER, ATTR_SUGGESTED_AREA, ATTR_NAME, ATTR_IDENTIFIERS, ATTR_DEVICE_ID

from . import DOMAIN as CASETA_DOMAIN, LutronCasetaDevice, _area_and_name_from_name
from .const import CONFIG_URL, MANUFACTURER
from .models import LutronCasetaData

from .const import (
    DOMAIN,
    MANUFACTURER,
    UNASSIGNED_AREA,
)

_LOGGER = logging.getLogger(__name__)


ATTR_BUTTON_NAME = "button_name"
ATTR_BUTTON_LED = "button_led"
ATTR_CONTROL_STATION_NAME = "control_station_name"
ATTR_BUTTON_GROUPS = "button_groups"
ATTR_BUTTON_GROUP = "button_group"
ATTR_TYPE = "type"

SEETOUCH_BUTTON_NAME_DEFAULT_PATTERN = "Button "
SEETOUCH_BUTTON_NAME_LOWER = "Button 18"
SEETOUCH_BUTTON_NAME_RAISE = "Button 19"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lutron Caseta binary_sensor platform.

    Adds occupancy groups from the Caseta bridge associated with the
    config_entry as binary_sensor entities.
    """
    data: LutronCasetaData = hass.data[CASETA_DOMAIN][config_entry.entry_id]
    bridge = data.bridge
    bridge_device = data.bridge_device
    occupancy_groups = bridge.occupancy_groups
    async_add_entities(
        LutronOccupancySensor(occupancy_group, bridge, bridge_device)
        for occupancy_group in occupancy_groups.values()
    )

    buttons = bridge.buttons
    devices = bridge.devices

    button_group_to_device_map = {}
    for device in devices.values():
        if ATTR_BUTTON_GROUPS in device and isinstance(device[ATTR_BUTTON_GROUPS], Iterable):
            for g in device[ATTR_BUTTON_GROUPS]:
                if g not in button_group_to_device_map:
                    button_group_to_device_map[g] = device

    led_entities = []
    for device_id in buttons:
        button_data = buttons[device_id]
        if ATTR_BUTTON_LED in button_data and button_data[ATTR_BUTTON_LED] is not None:
            _LOGGER.debug(f"async_setup_entry button_led: button_data={button_data}")

            keypad_led_device_id = button_data[ATTR_BUTTON_LED]
            keypad_led_device = devices[keypad_led_device_id]

            button_name = button_data[ATTR_BUTTON_NAME]
            if button_name not in [SEETOUCH_BUTTON_NAME_LOWER, SEETOUCH_BUTTON_NAME_RAISE]:
                button = LutronCasetaButtonLED(
                    keypad_led_device,
                    button_data,
                    bridge, 
                    bridge_device,
                    button_group_to_device_map[button_data[ATTR_BUTTON_GROUP]])
                led_entities.append(button)

    async_add_entities(led_entities)



class LutronOccupancySensor(LutronCasetaDevice, BinarySensorEntity):
    """Representation of a Lutron occupancy group."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, device, bridge, bridge_device):
        """Init an occupancy sensor."""
        super().__init__(device, bridge, bridge_device)
        _, name = _area_and_name_from_name(device["name"])
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(CASETA_DOMAIN, self.unique_id)},
            manufacturer=MANUFACTURER,
            model="Lutron Occupancy",
            name=self.name,
            via_device=(CASETA_DOMAIN, self._bridge_device["serial"]),
            configuration_url=CONFIG_URL,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self):
        """Return the brightness of the light."""
        return self._device["status"] == OCCUPANCY_GROUP_OCCUPIED

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        self._smartbridge.add_occupancy_subscriber(
            self.device_id, self.async_write_ha_state
        )

    @property
    def device_id(self):
        """Return the device ID used for calling pylutron_caseta."""
        return self._device["occupancy_group_id"]

    @property
    def unique_id(self):
        """Return a unique identifier."""
        return f"occupancygroup_{self._bridge_unique_id}_{self.device_id}"

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {"device_id": self.device_id}


class LutronCasetaButtonLED(BinarySensorEntity):
    """Representation of a Lutron Caseta button."""
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, led_device, button_device, bridge, bridge_device, parent_device):
        """Set up the base class.

        [:param]device the device metadata
        [:param]bridge the smartbridge object
        [:param]bridge_device a dict with the details of the bridge
        """
        self._device = led_device
        self._button_device = button_device
        self._smartbridge = bridge
        self._bridge_device = bridge_device
        self.device_id = str(led_device[ATTR_DEVICE_ID])

        button_name = button_device[ATTR_BUTTON_NAME]

        if SEETOUCH_BUTTON_NAME_DEFAULT_PATTERN in button_name:
            # Hide default buttons
            self._attr_entity_registry_enabled_default = False
        else:
            self._attr_entity_registry_enabled_default = True

        button_device_name = button_device[ATTR_NAME]
        area, _ = _button_area_and_name_from_name(button_device_name)

        self.display_name = f"{area} {button_name} LED"
        self._attr_name = self.display_name
        self._attr_unique_id = self.device_id

        parent_area, parent_name = _button_area_and_name_from_name(parent_device[ATTR_CONTROL_STATION_NAME])

        info = {
            ATTR_IDENTIFIERS: {(DOMAIN, parent_device[ATTR_DEVICE_ID])},
            ATTR_NAME: f"{parent_area} {parent_name}",
            ATTR_MANUFACTURER: MANUFACTURER,
            ATTR_MODEL: f"{button_device[ATTR_MODEL]} ({button_device[ATTR_TYPE]})",
        }

        if parent_area != UNASSIGNED_AREA:
            info[ATTR_SUGGESTED_AREA] = parent_area
        self._attr_device_info = info
    
    @property
    def name(self):
        return self.display_name

    @property
    def is_on(self) -> bool:
        """Return true if device is on."""
        return self._device["current_state"] > 0

    @property
    def icon(self) -> str:
        return "mdi:led-on" if self.is_on else "mdi:led-off"


def _button_area_and_name_from_name(device_name: str) -> tuple[str, str]:
    """Return the area and name from the devices internal name."""
    if "_" in device_name:
        area_device_name = device_name.split("_", 1)
        return area_device_name[0], area_device_name[1]
    return UNASSIGNED_AREA, device_name