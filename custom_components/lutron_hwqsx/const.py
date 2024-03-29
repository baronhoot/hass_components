"""Lutron Caseta constants."""

DOMAIN = "lutron_hwqsx"

CONF_KEYFILE = "keyfile"
CONF_CERTFILE = "certfile"
CONF_CA_CERTS = "ca_certs"

STEP_IMPORT_FAILED = "import_failed"
ERROR_CANNOT_CONNECT = "cannot_connect"
ABORT_REASON_CANNOT_CONNECT = "cannot_connect"

LUTRON_CASETA_BUTTON_EVENT = "lutron_caseta_button_event"

BRIDGE_DEVICE_ID = "1"

MANUFACTURER = "Lutron Electronics Co., Inc"

ATTR_SERIAL = "serial"
ATTR_TYPE = "type"
ATTR_LEAP_BUTTON_NUMBER = "leap_button_number"
ATTR_BUTTON_NUMBER = "button_number"  # LIP button number
ATTR_DEVICE_NAME = "device_name"
ATTR_AREA_NAME = "area_name"
ATTR_ACTION = "action"

ACTION_PRESS = "press"
ACTION_RELEASE = "release"

ACTION_PRESSED = "pressed"
ACTION_RELEASED = "released"

CONF_SUBTYPE = "subtype"

BRIDGE_TIMEOUT = 35

UNASSIGNED_AREA = "Unassigned"

CONFIG_URL = "https://device-login.lutron.com"
