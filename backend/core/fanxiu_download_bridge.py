"""Legacy compatibility alias for Fanxiu download bridge helpers.

Canonical implementation lives in ``backend.core.fanxiu.runtime.download_bridge``.
"""

import sys

from backend.core.fanxiu.runtime import download_bridge as _module

sys.modules[__name__] = _module
