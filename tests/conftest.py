import os
import sys

# UI tests must not need a display. On macOS the frameless-window helper needs the
# native platform plugin (it segfaults under "offscreen"); tests never show a window there.
if sys.platform != "darwin":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
