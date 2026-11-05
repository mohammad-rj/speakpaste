"""Platform switch: callers import OS calls from here, never from a backend."""
import sys

if sys.platform == "win32":
    from .win32 import *  # noqa: F401,F403
    from .win32 import _phys_down, _MODIFIERS  # noqa: F401
else:
    from .linux_x11 import *  # noqa: F401,F403
    from .linux_x11 import _phys_down, _MODIFIERS  # noqa: F401
