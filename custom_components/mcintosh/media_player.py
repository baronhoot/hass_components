"""Support for McIntosh Network Receivers."""
from __future__ import annotations

import logging
import telnetlib

import voluptuous as vol

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "DEFAULT AVR"

SUPPORT_MCINTOSH = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.SELECT_SOURCE
)
SUPPORT_MEDIA_MODES = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PLAY
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)

NORMAL_INPUTS = {
    "Cd": "CD",
    "Dvd": "DVD",
    "Blue ray": "BD",
    "TV": "TV",
    "Satellite / Cable": "SAT/CBL",
    "Game": "GAME",
    "Game2": "GAME2",
    "Video Aux": "V.AUX",
    "Dock": "DOCK",
}

MEDIA_MODES = {
    "Tuner": "TUNER",
    "Media server": "SERVER",
    "Ipod dock": "IPOD",
    "Net/USB": "NET/USB",
    "Rapsody": "RHAPSODY",
    "Napster": "NAPSTER",
    "Pandora": "PANDORA",
    "LastFM": "LASTFM",
    "Flickr": "FLICKR",
    "Favorites": "FAVORITES",
    "Internet Radio": "IRADIO",
    "USB/IPOD": "USB/IPOD",
}

# Sub-modes of 'NET/USB'
# {'USB': 'USB', 'iPod Direct': 'IPD', 'Internet Radio': 'IRP',
#  'Favorites': 'FVP'}


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the McIntosh platform."""
    mcintosh = McIntoshDevice(config[CONF_NAME], config[CONF_HOST])

    if mcintosh.do_update():
        add_entities([mcintosh])


class McIntoshDevice(MediaPlayerEntity):
    """Representation of a McIntosh device."""

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_icon = "mdi:audio-video"
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.SELECT_SOURCE
        )

    def __init__(self, name, host):
        """Initialize the McIntosh device."""
        _LOGGER.debug("initializing mcintosh: %s, %s", name, host)
        self._name = name
        self._host = host
        self._pwstate = "PWSTANDBY"
        self._volume = 0
        # Initial value 60dB, changed if we get a MVMAX
        self._volume_max = 98
        self._source_list = NORMAL_INPUTS.copy()
        self._source_list.update(MEDIA_MODES)
        self._muted = False
        self._mediasource = ""
        self._mediainfo = ""

        self._should_setup_sources = True

    def _setup_sources(self, telnet):
        _LOGGER.debug("_setup_sources: %s", telnet)
        # NSFRN - Network name
        if self._name == DEFAULT_NAME:
            nsfrn = self.telnet_request(telnet, "NSFRN ?")[len("NSFRN ") :]
            if nsfrn:
                self._name = nsfrn

        # SSFUN - Configured sources with (optional) names
        self._source_list = {}
        for line in self.telnet_request(telnet, "SSFUN ?", all_lines=True):
            ssfun = line[len("SSFUN") :].split(" ", 1)
            source = ssfun[0]
            if len(ssfun) == 2 and ssfun[1]:
                configured_name = ssfun[1]
            else:
                # No name configured, reusing the source name
                configured_name = source
            if configured_name == "END":
                continue
            self._source_list[configured_name] = source

        _LOGGER.debug(f"all sources: {self._source_list}")

        # SSSOD - Deleted sources
        for line in self.telnet_request(telnet, "SSSOD ?", all_lines=True):
            source, status = line[len("SSSOD") :].split(" ", 1)
            if status == "DEL":
                for pretty_name, name in self._source_list.items():
                    if source == name:
                        del self._source_list[pretty_name]
                        break

    @classmethod
    def telnet_request(cls, telnet, command, all_lines=False):
        """Execute `command` and return the response."""
        _LOGGER.debug("Sending: %s", command)
        telnet.write(command.encode("ASCII") + b"\r")
        lines = []
        while True:
            line = telnet.read_until(b"\r", timeout=0.2)
            if not line:
                break
            lines.append(line.decode("ASCII").strip())
            _LOGGER.debug("Received: %s", line)

        if all_lines:
            return lines
        return lines[0] if lines else ""

    @classmethod
    def get_telnet_response(cls, telnet, cmd, is_prefixed, default):
        if (
            response := cls.telnet_request(telnet, f"{cmd}?")
        ) and response.startswith(cmd):
            if is_prefixed:
                return response[len(cmd) :]
            else:
                return response
        else:
            _LOGGER.debug(f"get_telnet_response({cmd}?)={response}, returning default={default}")
            return default

    def telnet_command(self, command):
        """Establish a telnet connection and sends `command`."""
        telnet = telnetlib.Telnet(self._host)
        _LOGGER.debug("Sending: %s", command)
        telnet.write(command.encode("ASCII") + b"\r")
        telnet.read_very_eager()  # skip response
        telnet.close()

    def update(self) -> None:
        """Get the latest details from the device."""
        _LOGGER.debug("update: %s", self)
        self.do_update()

    def do_update(self) -> bool:
        """Get the latest details from the device, as boolean."""
        _LOGGER.debug("do_update: %s", self)
        try:
            telnet = telnetlib.Telnet(self._host)
        except OSError:
            _LOGGER.error("OSError from do_update")
            return False

        if self._should_setup_sources:
            self._setup_sources(telnet)
            self._should_setup_sources = False

        self._pwstate = self.get_telnet_response(telnet, "PW", False, self._pwstate)
        self.handle_volume_response(self.telnet_request(telnet, "MV?", all_lines=True))
        self._muted = self.get_telnet_response(telnet, "MU", False, self._muted) == "MUON"
        self._mediasource = self.get_telnet_response(telnet, "SI", True, self._mediasource)

        if self._mediasource in MEDIA_MODES.values():
            self._mediainfo = ""
            answer_codes = [
                "NSE0",
                "NSE1X",
                "NSE2X",
                "NSE3X",
                "NSE4",
                "NSE5",
                "NSE6",
                "NSE7",
                "NSE8",
            ]
            for line in self.telnet_request(telnet, "NSE", all_lines=True):
                self._mediainfo += f"{line[len(answer_codes.pop(0)) :]}\n"
        else:
            self._mediainfo = self.source

        telnet.close()
        return True

    def handle_volume_response(self, response) -> int:
        for line in response:
            # only grab two digit max, don't care about any half digit
            if line.startswith("MVMAX "):
                self._volume_max = int(line[len("MVMAX ") : len("MVMAX XX")])
                continue
            elif line.startswith("MV"):
                self._volume = int(line[len("MV") : len("MVXX")])
        return self._volume

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self) -> MediaPlayerState | None:
        """Return the state of the device."""
        _LOGGER.debug(f"state(): {self._pwstate}")
        if self._pwstate == "PWSTANDBY":
            return MediaPlayerState.OFF
        elif self._pwstate == "PWON":
            return MediaPlayerState.ON
        else:
            return None

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        _LOGGER.debug(f"volume_level(): {self._volume} / {self._volume_max}")
        return self._volume / self._volume_max

    @property
    def is_volume_muted(self):
        """Return boolean if volume is currently muted."""
        return self._muted

    @property
    def source_list(self):
        """Return the list of available input sources."""
        return sorted(self._source_list)

    @property
    def media_title(self):
        """Return the current media info."""
        return self._mediainfo

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        if self._mediasource in MEDIA_MODES.values():
            return SUPPORT_MCINTOSH | SUPPORT_MEDIA_MODES
        return SUPPORT_MCINTOSH

    @property
    def source(self):
        """Return the current input source."""
        for pretty_name, name in self._source_list.items():
            if self._mediasource == name:
                return pretty_name

    def turn_on(self) -> None:
        """Turn the media player on."""
        telnet = telnetlib.Telnet(self._host)
        self._pwstate = "PWON"
        self._pwstate = self.telnet_request(telnet, "PWON")

    def turn_off(self) -> None:
        """Turn off media player."""
        telnet = telnetlib.Telnet(self._host)
        self._pwstate = "PWSTANDBY"
        self._pwstate = self.telnet_request(telnet, "PWSTANDBY")

    def select_source(self, source: str) -> None:
        """Select input source."""
        telnet = telnetlib.Telnet(self._host)
        updated_source = self._source_list.get(source)
        self._mediasource = source
        self._mediasource = self.telnet_request(telnet, f"SI{updated_source}")[2 :]

    def set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        telnet = telnetlib.Telnet(self._host)
        self._volume = round(volume * self._volume_max)
        self.handle_volume_response(
            self.telnet_request(telnet, f"MV{self._volume:02}", all_lines=True))

    def volume_up(self) -> None:
        """Volume up media player."""
        telnet = telnetlib.Telnet(self._host)
        self.handle_volume_response(
            self.telnet_request(telnet, "MVUP", all_lines=True))

    def volume_down(self) -> None:
        """Volume down media player."""
        telnet = telnetlib.Telnet(self._host)
        self.handle_volume_response(
            self.telnet_request(telnet, "MVDOWN", all_lines=True))

    def mute_volume(self, mute: bool) -> None:
        """Mute (true) or unmute (false) media player."""
        telnet = telnetlib.Telnet(self._host)

        mute_status = "ON" if mute else "OFF"
        self._muted = mute
        self._muted = self.telnet_request(telnet, f"MU{mute_status}") == "MUON"

    def media_play(self) -> None:
        """Play media player."""
        self.telnet_command("NS9A")

    def media_pause(self) -> None:
        """Pause media player."""
        self.telnet_command("NS9B")

    def media_stop(self) -> None:
        """Pause media player."""
        self.telnet_command("NS9C")

    def media_next_track(self) -> None:
        """Send the next track command."""
        self.telnet_command("NS9D")

    def media_previous_track(self) -> None:
        """Send the previous track command."""
        self.telnet_command("NS9E")