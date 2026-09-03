"""Legacy compatibility alias for Fanxiu MuMu control helpers.

Canonical implementation lives in ``backend.core.fanxiu.client.mumu_control``.
"""

import sys

from backend.core.fanxiu.client import mumu_control as _module

sys.modules[__name__] = _module
