"""Support for Lutron Caseta buttons."""

from typing import Any, Dict
import logging
from collections.abc import Iterable

from homeassistant.components.button import (
    ButtonEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_MODEL, ATTR_MANUFACTURER, ATTR_SUGGESTED_AREA, ATTR_NAME, ATTR_IDENTIFIERS, ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pylutron_caseta.smartbridge import Smartbridge

from .models import LutronCasetaData
from .util import serial_to_unique_id

from .const import DOMAIN as CASETA_DOMAIN
from .const import (
    DOMAIN,
    MANUFACTURER,
    UNASSIGNED_AREA,
)

_LOGGER = logging.getLogger(__name__)

ATTR_BUTTON_NAME = "button_name"
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
    """Set up the Lutron Caseta button platform.

    Adds buttons from the Caseta bridge associated with the config_entry as
    button entities.
    """
    data: LutronCasetaData = hass.data[CASETA_DOMAIN][config_entry.entry_id]
    bridge = data.bridge
    bridge_device = data.bridge_device
    buttons = bridge.buttons

    button_group_to_device_map = {}
    for device in bridge.devices.values():
        if ATTR_BUTTON_GROUPS in device and isinstance(device[ATTR_BUTTON_GROUPS], Iterable):
            for g in device[ATTR_BUTTON_GROUPS]:
                if g not in button_group_to_device_map:
                    button_group_to_device_map[g] = device

    button_entities = []
    for device_id in buttons:
        button_data = buttons[device_id]
        if ATTR_BUTTON_GROUP in button_data:
            button = LutronCasetaButton(
                buttons[device_id], 
                bridge, 
                bridge_device,
                button_group_to_device_map[button_data[ATTR_BUTTON_GROUP]])
            button_entities.append(button)

    async_add_entities(button_entities)

class LutronCasetaButton(ButtonEntity):
    """Representation of a Lutron Caseta button."""
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:light-switch"

    def __init__(self, device, bridge, bridge_device, parent_device):
        """Set up the base class.

        [:param]device the device metadata
        [:param]bridge the smartbridge object
        [:param]bridge_device a dict with the details of the bridge
        """
        self._device = device
        self._smartbridge = bridge
        self._bridge_device = bridge_device
        self.device_id = str(device[ATTR_DEVICE_ID])

        button_name = device[ATTR_BUTTON_NAME]
        if button_name == SEETOUCH_BUTTON_NAME_LOWER:
            button_name = "Lower"
        elif button_name == SEETOUCH_BUTTON_NAME_RAISE:
            button_name = "Raise"
        
        if SEETOUCH_BUTTON_NAME_DEFAULT_PATTERN in button_name:
            # Hide default buttons
            self._attr_entity_registry_enabled_default = False
        else:
            self._attr_entity_registry_enabled_default = True

        device_name = device[ATTR_NAME]
        area, _ = _area_and_name_from_name(device_name)

        self.display_name = f"{area} {button_name}"
        self._attr_name = self.display_name
        self._attr_unique_id = self.device_id

        parent_area, parent_name = _area_and_name_from_name(parent_device[ATTR_CONTROL_STATION_NAME])

        info = {
            ATTR_IDENTIFIERS: {(DOMAIN, parent_device[ATTR_DEVICE_ID])},
            ATTR_NAME: f"{parent_area} {parent_name}",
            ATTR_MANUFACTURER: MANUFACTURER,
            ATTR_MODEL: f"{device[ATTR_MODEL]} ({device[ATTR_TYPE]})",
        }

        if parent_area != UNASSIGNED_AREA:
            info[ATTR_SUGGESTED_AREA] = parent_area
        self._attr_device_info = info

    async def async_press(self) -> None:
        """Send out a command."""
        await self._smartbridge.tap_button(self.device_id)
    
    @property
    def name(self):
        return self.display_name

def _area_and_name_from_name(device_name: str) -> tuple[str, str]:
    """Return the area and name from the devices internal name."""
    if "_" in device_name:
        area_device_name = device_name.split("_", 1)
        return area_device_name[0], area_device_name[1]
    return UNASSIGNED_AREA, device_name