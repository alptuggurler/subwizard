"""SubWizard entry point: `uv run run.py [options]`."""

import faulthandler
import sys

from subwizard.cli import main

# A hard crash or hang (kill -ABRT) still leaves a Python traceback. A --windowed
# Windows build has no stderr, so only when there is one.
if sys.stderr is not None:
    faulthandler.enable()

if __name__ == "__main__":
    sys.exit(main())
