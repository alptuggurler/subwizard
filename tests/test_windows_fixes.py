"""Fixes found by the first run on real Windows hardware (V-Z632 capture card)."""

import pytest

from subwizard.audio.devices import AudioDevice, guess_capture_input, wasapi_settings
from subwizard.core.session import _channels
from subwizard.platform.windows.devices import modes_from_formats
from subwizard.sources.base import VideoMode

INTERVAL_60 = 10_000_000 / 60  # DirectShow frame intervals are in 100 ns units
INTERVAL_10 = 10_000_000 / 10


def _fmt(width, height, subtype, fastest_interval, slowest_interval):
    # pygrabber's (inverted) naming: min_framerate comes from MinFrameInterval.
    return {
        "width": width,
        "height": height,
        "media_type_str": subtype,
        "min_framerate": 10_000_000 / fastest_interval,
        "max_framerate": 10_000_000 / slowest_interval,
    }


def test_capture_card_modes_show_their_fastest_rate_not_their_slowest():
    """Every V-Z632 mode was listed as @10 FPS: the slowest rate was being read."""
    modes = modes_from_formats([
        _fmt(1920, 1080, "MJPG", INTERVAL_60, INTERVAL_10),
        _fmt(1920, 1080, "YUY2", INTERVAL_60, INTERVAL_10),
    ])  # fmt: skip
    assert modes[0] == VideoMode(1920, 1080, 60.0, "MJPG")
    assert VideoMode(1920, 1080, 30.0, "MJPG") in modes  # standard rates within range
    assert VideoMode(1920, 1080, 10.0, "MJPG") not in modes
    assert all(m.fps >= 25 for m in modes)


def test_ntsc_rate_is_offered_as_60_and_out_of_range_rates_are_not():
    modes = modes_from_formats([_fmt(1280, 720, "MJPG", 10_000_000 / 59.94, 10_000_000 / 30)])
    assert [m.fps for m in modes] == [60.0, 50.0, 30.0]


def test_single_rate_formats_and_junk_entries():
    fixed = _fmt(640, 480, "YUY2", INTERVAL_10, INTERVAL_10)
    junk = [{"width": 0, "height": 480}, {"width": 640, "height": 480, "min_framerate": 0}]
    assert modes_from_formats([fixed, *junk]) == [VideoMode(640, 480, 10.0, "YUY2")]


INPUTS = [
    AudioDevice(44, "Mikrofon (Arctis Nova 7 Gen 2)", "Windows WASAPI", 1, 0, 48000),
    AudioDevice(46, "Dijital Ses Arabirimi (V-Z632)", "Windows WASAPI", 2, 0, 48000),
]
OUTPUTS = [
    AudioDevice(34, "Kulaklıklar (Arctis Nova 7 Gen 2)", "Windows WASAPI", 0, 2, 48000),
    AudioDevice(37, "SteelSeries Sonar - Gaming", "Windows WASAPI", 0, 8, 96000, True),
]


@pytest.mark.parametrize(
    ("video", "expected"),
    [("V-Z632", "Dijital Ses Arabirimi (V-Z632)"), ("Chicony USB2.0 Camera", None), ("", None)],
)
def test_capture_audio_input_is_found_by_the_card_name(video, expected):
    assert guess_capture_input(video, INPUTS) == expected


def test_both_streams_get_a_channel_count_every_device_supports():
    assert _channels(2, 46, 34, INPUTS, OUTPUTS) == 2
    assert _channels(2, 44, 34, INPUTS, OUTPUTS) == 1  # mono input
    assert _channels(2, 46, None, INPUTS, OUTPUTS) == 2  # default output (8 ch) is fine


def test_wasapi_rate_conversion_only_on_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    assert wasapi_settings() is None


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        ("SteelSeries Sonar - Microphone", "Dijital Ses Arabirimi (V-Z632)"),
        ("Dijital Ses Arabirimi (V-Z632)", None),
        (None, None),  # nothing chosen yet: the controller auto-selects instead
    ],
)
def test_warns_when_the_selected_input_is_not_the_cards_own(selected, expected):
    """Real setup: input was "SteelSeries Sonar - Microphone", so no PS5 sound came in."""
    from subwizard.audio.devices import other_input_than_card

    names = [d.name for d in INPUTS]
    assert other_input_than_card(selected, "V-Z632", names) == expected
    assert other_input_than_card(selected, "V-Z632", INPUTS) == expected
    assert other_input_than_card(selected, "Unknown Cam", INPUTS) is None


class _FakeSd:
    """PortAudio's global default is MME (index 1/3); WASAPI's own defaults are 46/37."""

    class default:  # noqa: N801
        device = (1, 3)

    @staticmethod
    def query_hostapis():
        return [
            {"name": "MME", "default_input_device": 1, "default_output_device": 3},
            {"name": "Windows WASAPI", "default_input_device": 46, "default_output_device": 37},
        ]

    @staticmethod
    def query_devices():
        return [
            {"index": 3, "name": "Speakers", "hostapi": 0, "max_input_channels": 0,
             "max_output_channels": 2, "default_samplerate": 44100},
            {"index": 37, "name": "SteelSeries Sonar - Gaming", "hostapi": 1,
             "max_input_channels": 0, "max_output_channels": 8, "default_samplerate": 96000},
        ]  # fmt: skip


def test_system_default_resolves_to_the_wasapi_default_not_mme(monkeypatch):
    """Regression: output "Sistem varsayılanı" (None) opened PortAudio's MME default with WASAPI
    settings -> "Incompatible host API specific stream info [PaErrorCode -9984]"."""
    import sys

    from subwizard.audio import devices

    monkeypatch.setitem(sys.modules, "sounddevice", _FakeSd)
    monkeypatch.setattr(devices, "PREFERRED_HOSTAPI", "WASAPI")
    assert devices.stream_device(None, "output") == 37
    assert devices.stream_device(None, "input") == 46
    assert devices.stream_device(5, "output") == 5
    [sonar] = devices.list_audio_devices("output")
    assert sonar.is_default  # was never marked: the MME index never matched a WASAPI one


def test_system_default_stays_none_off_windows(monkeypatch):
    from subwizard.audio import devices

    monkeypatch.setattr(devices, "PREFERRED_HOSTAPI", None)
    assert devices.stream_device(None, "output") is None
