"""Legacy compatibility alias for Fanxiu MuMu runtime helpers.

Canonical implementation lives in ``backend.core.fanxiu.runtime.mumu_control``.
"""

import sys

from backend.core.fanxiu.runtime import mumu_control as _module

sys.modules[__name__] = _module
