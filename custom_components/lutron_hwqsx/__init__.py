"""Component for interacting with a Lutron Caseta system."""
from __future__ import annotations

import asyncio
import contextlib
from itertools import chain
from collections.abc import Iterable
import logging
import ssl
from typing import Any

import async_timeout
from pylutron_caseta import BUTTON_STATUS_PRESSED
from pylutron_caseta.smartbridge import Smartbridge
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, ATTR_SUGGESTED_AREA, CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.typing import ConfigType

from .const import (
    ACTION_PRESSED,
    ACTION_RELEASED,
    ATTR_ACTION,
    ATTR_AREA_NAME,
    ATTR_BUTTON_NUMBER,
    ATTR_DEVICE_NAME,
    ATTR_LEAP_BUTTON_NUMBER,
    ATTR_SERIAL,
    ATTR_TYPE,
    BRIDGE_DEVICE_ID,
    BRIDGE_TIMEOUT,
    CONF_CA_CERTS,
    CONF_CERTFILE,
    CONF_KEYFILE,
    CONFIG_URL,
    DOMAIN,
    LUTRON_CASETA_BUTTON_EVENT,
    MANUFACTURER,
    UNASSIGNED_AREA,
)
from .device_trigger import (
    DEVICE_TYPE_SUBTYPE_MAP_TO_LIP,
    LEAP_TO_DEVICE_TYPE_SUBTYPE_MAP,
    _lutron_model_to_device_type,
)
from .models import LutronCasetaData
from .util import serial_to_unique_id

_LOGGER = logging.getLogger(__name__)

DATA_BRIDGE_CONFIG = "lutron_caseta_bridges"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            cv.ensure_list,
            [
                {
                    vol.Required(CONF_HOST): cv.string,
                    vol.Required(CONF_KEYFILE): cv.string,
                    vol.Required(CONF_CERTFILE): cv.string,
                    vol.Required(CONF_CA_CERTS): cv.string,
                }
            ],
        )
    },
    extra=vol.ALLOW_EXTRA,
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.COVER,
    Platform.FAN,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.SWITCH,
    Platform.BUTTON,
]

ATTR_CONTROL_STATION_NAME = "control_station_name"
ATTR_BUTTON_GROUPS = "button_groups"
ATTR_BUTTON_GROUP = "button_group"
ATTR_BUTTON_NAME = "button_name"

SEETOUCH_BUTTON_NAME_LOWER = "Button 18"
SEETOUCH_BUTTON_NAME_RAISE = "Button 19"

QSX_KEYPADS = {'SeeTouchHybridKeypad', 'HomeownerKeypad', 'SeeTouchTabletopKeypad', 'Pico3ButtonRaiseLower'}

async def async_setup(hass: HomeAssistant, base_config: ConfigType) -> bool:
    """Set up the Lutron component."""
    hass.data.setdefault(DOMAIN, {})

    if DOMAIN in base_config:
        bridge_configs = base_config[DOMAIN]
        for config in bridge_configs:
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": config_entries.SOURCE_IMPORT},
                    # extract the config keys one-by-one just to be explicit
                    data={
                        CONF_HOST: config[CONF_HOST],
                        CONF_KEYFILE: config[CONF_KEYFILE],
                        CONF_CERTFILE: config[CONF_CERTFILE],
                        CONF_CA_CERTS: config[CONF_CA_CERTS],
                    },
                )
            )

    return True


async def _async_migrate_unique_ids(
    hass: HomeAssistant, entry: config_entries.ConfigEntry
) -> None:
    """Migrate entities since the occupancygroup were not actually unique."""

    dev_reg = dr.async_get(hass)
    bridge_unique_id = entry.unique_id

    @callback
    def _async_migrator(entity_entry: er.RegistryEntry) -> dict[str, Any] | None:
        if not (unique_id := entity_entry.unique_id):
            return None
        if not unique_id.startswith("occupancygroup_") or unique_id.startswith(
            f"occupancygroup_{bridge_unique_id}"
        ):
            return None
        sensor_id = unique_id.split("_")[1]
        new_unique_id = f"occupancygroup_{bridge_unique_id}_{sensor_id}"
        if dev_entry := dev_reg.async_get_device({(DOMAIN, unique_id)}):
            dev_reg.async_update_device(
                dev_entry.id, new_identifiers={(DOMAIN, new_unique_id)}
            )
        return {"new_unique_id": f"occupancygroup_{bridge_unique_id}_{sensor_id}"}

    await er.async_migrate_entries(hass, entry.entry_id, _async_migrator)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: config_entries.ConfigEntry
) -> bool:
    """Set up a bridge from a config entry."""
    entry_id = config_entry.entry_id
    host = config_entry.data[CONF_HOST]
    keyfile = hass.config.path(config_entry.data[CONF_KEYFILE])
    certfile = hass.config.path(config_entry.data[CONF_CERTFILE])
    ca_certs = hass.config.path(config_entry.data[CONF_CA_CERTS])
    bridge = None

    try:
        bridge = Smartbridge.create_tls(
            hostname=host, keyfile=keyfile, certfile=certfile, ca_certs=ca_certs
        )
    except ssl.SSLError:
        _LOGGER.error("Invalid certificate used to connect to bridge at %s", host)
        return False

    timed_out = True
    with contextlib.suppress(asyncio.TimeoutError):
        async with async_timeout.timeout(BRIDGE_TIMEOUT):
            await bridge.connect()
            timed_out = False

    if timed_out or not bridge.is_connected():
        await bridge.close()
        if timed_out:
            raise ConfigEntryNotReady(f"Timed out while trying to connect to {host}")
        if not bridge.is_connected():
            raise ConfigEntryNotReady(f"Cannot connect to {host}")

    _LOGGER.debug("Connected to Lutron Caseta bridge via LEAP at %s", host)
    await _async_migrate_unique_ids(hass, config_entry)

    devices = bridge.get_devices()
    bridge_device = devices[BRIDGE_DEVICE_ID]
    if not config_entry.unique_id:
        hass.config_entries.async_update_entry(
            config_entry, unique_id=serial_to_unique_id(bridge_device["serial"])
        )

    buttons = bridge.buttons

    button_group_to_device_map: dict[str, dict] = {}
    for device in devices.values():
        if ATTR_BUTTON_GROUPS in device and isinstance(device[ATTR_BUTTON_GROUPS], Iterable):
            for g in device[ATTR_BUTTON_GROUPS]:
                if g not in button_group_to_device_map:
                    button_group_to_device_map[g] = device

    _LOGGER.debug(f"button_group_to_device_map: {button_group_to_device_map}")

    _async_register_bridge_device(hass, entry_id, bridge_device)
    button_devices = _async_register_button_devices(
        hass, entry_id, bridge_device, buttons, button_group_to_device_map
    )

    _async_subscribe_pico_remote_events(hass, bridge, buttons, button_group_to_device_map)

    # Store this bridge (keyed by entry_id) so it can be retrieved by the
    # platforms we're setting up.
    hass.data[DOMAIN][entry_id] = LutronCasetaData(
        bridge, bridge_device, button_devices
    )

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


@callback
def _async_register_bridge_device(
    hass: HomeAssistant, config_entry_id: str, bridge_device: dict
) -> None:
    """Register the bridge device in the device registry."""
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        name=bridge_device["name"],
        manufacturer=MANUFACTURER,
        config_entry_id=config_entry_id,
        identifiers={(DOMAIN, bridge_device["serial"])},
        model=f"{bridge_device['model']} ({bridge_device['type']})",
        configuration_url="https://device-login.lutron.com",
    )


@callback
def _async_register_button_devices(
    hass: HomeAssistant,
    config_entry_id: str,
    bridge_device,
    button_devices_by_id: dict[int, dict],
    button_group_to_device_map: dict[str, dict]
) -> dict[str, dict]:
    """Register button devices (Pico Remotes) in the device registry."""
    device_registry = dr.async_get(hass)
    button_devices_by_dr_id: dict[str, dict] = {}
    seen = set()

    for device in button_devices_by_id.values():
        if ATTR_SERIAL not in device and ATTR_BUTTON_GROUP in device:
            device[ATTR_SERIAL] = int(device[ATTR_BUTTON_GROUP])
        
        if device[ATTR_SERIAL] in seen:
            continue
        seen.add(device[ATTR_SERIAL])

        _LOGGER.debug(f"_async_register_button_devices: {device}")
        if device['type'] in QSX_KEYPADS:
            # Homeworks Keypad Handling
            button_group = device[ATTR_BUTTON_GROUP]
            parent_device = button_group_to_device_map[button_group]
            hass_device_id = parent_device[ATTR_DEVICE_ID]
        else:
            hass_device_id = device[ATTR_SERIAL]

        area, name = _area_and_name_from_name(device["name"])
        device_args: dict[str, Any] = {
            "name": f"{area} {name}",
            "manufacturer": MANUFACTURER,
            "config_entry_id": config_entry_id,
            "identifiers": {(DOMAIN, hass_device_id)},
            "model": f"{device['model']} ({device['type']})",
            "via_device": (DOMAIN, bridge_device[ATTR_SERIAL]),
        }
        if area != UNASSIGNED_AREA:
            device_args["suggested_area"] = area

        dr_device = device_registry.async_get_or_create(**device_args)
        button_devices_by_dr_id[dr_device.id] = device

    return button_devices_by_dr_id


def _area_and_name_from_name(device_name: str) -> tuple[str, str]:
    """Return the area and name from the devices internal name."""
    if "_" in device_name:
        area_device_name = device_name.split("_", 1)
        return area_device_name[0], area_device_name[1]
    return UNASSIGNED_AREA, device_name


@callback
def async_get_lip_button(device_type: str, leap_button: int) -> int | None:
    """Get the LIP button for a given LEAP button."""
    if (
        lip_buttons_name_to_num := DEVICE_TYPE_SUBTYPE_MAP_TO_LIP.get(device_type)
    ) is None or (
        leap_button_num_to_name := LEAP_TO_DEVICE_TYPE_SUBTYPE_MAP.get(device_type)
    ) is None:
        return None
    return lip_buttons_name_to_num[leap_button_num_to_name[leap_button]]


@callback
def _async_subscribe_pico_remote_events(
    hass: HomeAssistant,
    bridge_device: Smartbridge,
    button_devices_by_id: dict[int, dict],
    button_group_to_device_map: dict[str, dict]
):
    """Subscribe to lutron events."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    @callback
    def _async_button_event(button_id, event_type):
        if not (device := button_devices_by_id.get(button_id)):
            return

        if event_type == BUTTON_STATUS_PRESSED:
            action = ACTION_PRESSED
        else:
            action = ACTION_RELEASED

        type_ = _lutron_model_to_device_type(device["model"], device["type"])
        area, name = _area_and_name_from_name(device["name"])
        leap_button_number = device["button_number"]
        lip_button_number = async_get_lip_button(type_, leap_button_number)

        if device['type'] in QSX_KEYPADS:
            # Homeworks Keypad Handling
            button_group = device[ATTR_BUTTON_GROUP]
            parent_device = button_group_to_device_map[button_group]
            hass_device_id = parent_device[ATTR_DEVICE_ID]
            area, name = _area_and_name_from_name(parent_device[ATTR_CONTROL_STATION_NAME])
            button_name = device[ATTR_BUTTON_NAME]
            if button_name == SEETOUCH_BUTTON_NAME_LOWER:
                button_name = "Lower"
            elif button_name == SEETOUCH_BUTTON_NAME_RAISE:
                button_name = "Raise"
        else:
            hass_device_id = device["serial"]
            button_name = f"button_{leap_button_number}"

        hass_device = dev_reg.async_get_device({(DOMAIN, hass_device_id)})
        hass_entity_id = ent_reg.async_get_entity_id(
            Platform.BUTTON, DOMAIN, button_id)

        hass.bus.async_fire(
            LUTRON_CASETA_BUTTON_EVENT,
            {
                ATTR_SERIAL: hass_device_id,
                ATTR_TYPE: type_,
                ATTR_BUTTON_NUMBER: lip_button_number,
                ATTR_BUTTON_NAME: button_name,
                ATTR_LEAP_BUTTON_NUMBER: leap_button_number,
                ATTR_DEVICE_NAME: name,
                ATTR_DEVICE_ID: hass_device.id,
                ATTR_AREA_NAME: area,
                ATTR_ACTION: action,
                ATTR_ENTITY_ID: hass_entity_id,
            },
        )

    for button_id in button_devices_by_id:
        bridge_device.add_button_subscriber(
            str(button_id),
            lambda event_type, button_id=button_id: _async_button_event(
                button_id, event_type
            ),
        )


async def async_unload_entry(
    hass: HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload the bridge bridge from a config entry."""
    data: LutronCasetaData = hass.data[DOMAIN][entry.entry_id]
    await data.bridge.close()
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class LutronCasetaDevice(Entity):
    """Common base class for all Lutron Caseta devices."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device, bridge, bridge_device):
        """Set up the base class.

        [:param]device the device metadata
        [:param]bridge the smartbridge object
        [:param]bridge_device a dict with the details of the bridge
        """
        self._device = device
        self._smartbridge = bridge
        self._bridge_device = bridge_device

        if "serial" not in self._device:
            return

        self._bridge_unique_id = serial_to_unique_id(bridge_device["serial"])
        area, name = _area_and_name_from_name(device["name"])

        self.display_name = f"{area} {name}"
        self._attr_name = self.display_name
        self._attr_unique_id = self.serial
        info = DeviceInfo(
            identifiers={(DOMAIN, self._handle_none_serial(self.serial))},
            manufacturer=MANUFACTURER,
            model=f"{device['model']} ({device['type']})",
            name=self.display_name,
            via_device=(DOMAIN, self._bridge_device["serial"]),
            configuration_url=CONFIG_URL,
        )
        if area != UNASSIGNED_AREA:
            info[ATTR_SUGGESTED_AREA] = area
        self._attr_device_info = info

    @property
    def name(self):
        return self.display_name

    async def async_added_to_hass(self):
        """Register callbacks."""
        self._smartbridge.add_subscriber(self.device_id, self.async_write_ha_state)

    def _handle_none_serial(self, serial: str | None) -> str | int:
        """Handle None serial returned by RA3 and QSX processors."""
        sret = serial
        if serial is None:
            sret=f"{self._bridge_unique_id}_{self.device_id}"
        if self._device.get("button_number") is not None:
            sret += '-' + self._device["button_number"]
        return sret

    @property
    def device_id(self):
        """Return the device ID used for calling pylutron_caseta."""
        return self._device["device_id"]

    @property
    def serial(self):
        """Return the serial number of the device."""
        return self._device["serial"]

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the device (serial)."""
        return str(self._handle_none_serial(self.serial))

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        zone=self._device.get("zone", "1")
        return {"device_id": self.device_id, "zone_id": zone}


class LutronCasetaDeviceUpdatableEntity(LutronCasetaDevice):
    """A lutron_caseta entity that can update by syncing data from the bridge."""

    async def async_update(self) -> None:
        """Update when forcing a refresh of the device."""
        self._device = self._smartbridge.get_device_by_id(self.device_id)
        _LOGGER.debug(self._device)


def _id_to_identifier(lutron_id: str) -> tuple[str, str]:
    """Convert a lutron caseta identifier to a device identifier."""
    return (DOMAIN, lutron_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: config_entries.ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Remove lutron_caseta config entry from a device."""
    data: LutronCasetaData = hass.data[DOMAIN][entry.entry_id]
    bridge = data.bridge
    devices = bridge.get_devices()
    buttons = bridge.buttons
    occupancy_groups = bridge.occupancy_groups
    bridge_device = devices[BRIDGE_DEVICE_ID]
    bridge_unique_id = serial_to_unique_id(bridge_device["serial"])
    all_identifiers: set[tuple[str, str]] = {
        # Base bridge
        _id_to_identifier(bridge_unique_id),
        # Motion sensors and occupancy groups
        *(
            _id_to_identifier(
                f"occupancygroup_{bridge_unique_id}_{device['occupancy_group_id']}"
            )
            for device in occupancy_groups.values()
        ),
        # Button devices such as pico remotes and all other devices
        *(
            _id_to_identifier(device["serial"])
            for device in chain(devices.values(), buttons.values())
        ),
    }
    return not any(
        identifier
        for identifier in device_entry.identifiers
        if identifier in all_identifiers
    )
