"""DirectShow device enumeration via pygrabber (Windows only).

The DirectShow order matches the device index OpenCV's CAP_DSHOW backend uses,
which is why a name can be resolved to an index here.
"""

from __future__ import annotations

import logging

from subwizard.platform import VideoDevice
from subwizard.sources.base import VideoMode

log = logging.getLogger(__name__)

# DirectShow reports subtype names; the capture code asks OpenCV for FOURCCs.
_SUBTYPE_TO_FOURCC = {"MJPG": "MJPG", "YUY2": "YUY2", "NV12": "NV12", "RGB24": "BGR3"}


def list_video_devices() -> list[VideoDevice]:
    try:
        from pygrabber.dshow_graph import FilterGraph

        names = FilterGraph().get_input_devices()
    except Exception as exc:  # COM errors surface as assorted exception types
        log.warning("Video cihazları listelenemedi: %s", exc)
        return []
    return [VideoDevice(index, name) for index, name in enumerate(names)]


# Frame rates offered in addition to each format's fastest one, when the card allows them.
STANDARD_FPS = (60.0, 50.0, 30.0, 25.0)
SNAP_TOLERANCE_FPS = 0.1  # 59.94 is offered as 60 (what OpenCV is then asked for)


def _snap(fps: float) -> float:
    nearest = float(round(fps))
    return nearest if abs(fps - nearest) <= SNAP_TOLERANCE_FPS else round(fps, 2)


def modes_from_formats(formats: list[dict]) -> list[VideoMode]:
    """Turn pygrabber's format list into selectable modes, best first.

    pygrabber's names are inverted: its `min_framerate` is computed from
    MinFrameInterval, i.e. the *fastest* rate, and `max_framerate` from
    MaxFrameInterval, the *slowest*. Reading `max_framerate` made every mode of a
    60 FPS capture card show up as 10 FPS. Both are read here and ordered.
    """
    modes = set()
    for fmt in formats:
        width, height = int(fmt.get("width") or 0), int(fmt.get("height") or 0)
        rates = [float(fmt.get(k) or 0) for k in ("min_framerate", "max_framerate")]
        rates = [r for r in rates if r > 0]
        if not (width and height and rates):
            continue
        slowest, fastest = min(rates), max(rates)
        subtype = str(fmt.get("media_type_str", "")).upper()
        fourcc = _SUBTYPE_TO_FOURCC.get(subtype, subtype[:4] or "?")
        offered = {_snap(fastest)}
        offered.update(r for r in STANDARD_FPS if slowest - SNAP_TOLERANCE_FPS <= r <= fastest + SNAP_TOLERANCE_FPS)
        for fps in offered:
            modes.add(VideoMode(width, height, fps, fourcc))
    return sorted(
        modes, key=lambda m: (m.width * m.height, m.fps, m.fourcc == "MJPG"), reverse=True
    )


def list_video_modes(index: int) -> list[VideoMode]:
    try:
        from pygrabber.dshow_graph import FilterGraph

        graph = FilterGraph()
        graph.add_video_input_device(index)
        formats = graph.get_input_device().get_formats()
    except Exception as exc:
        log.warning("Cihaz %s için mod listesi alınamadı: %s", index, exc)
        return []
    return modes_from_formats(formats)
