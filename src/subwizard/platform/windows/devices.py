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


def list_video_modes(index: int) -> list[VideoMode]:
    try:
        from pygrabber.dshow_graph import FilterGraph

        graph = FilterGraph()
        graph.add_video_input_device(index)
        formats = graph.get_input_device().get_formats()
    except Exception as exc:
        log.warning("Cihaz %s için mod listesi alınamadı: %s", index, exc)
        return []

    modes = set()
    for fmt in formats:
        subtype = str(fmt.get("media_type_str", "")).upper()
        fps = fmt.get("max_framerate") or 0
        if not fps or not fmt.get("width") or not fmt.get("height"):
            continue
        modes.add(
            VideoMode(
                int(fmt["width"]),
                int(fmt["height"]),
                round(float(fps), 2),
                _SUBTYPE_TO_FOURCC.get(subtype, subtype[:4] or "?"),
            )
        )
    return sorted(modes, key=lambda m: (m.width * m.height, m.fps, m.fourcc == "MJPG"), reverse=True)
