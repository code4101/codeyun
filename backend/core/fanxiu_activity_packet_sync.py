"""Legacy compatibility alias for Fanxiu activity packet sync helpers.

Canonical implementation lives in ``backend.core.fanxiu.packet.activity_sync``.
"""

import sys

from backend.core.fanxiu.packet import activity_sync as _module

sys.modules[__name__] = _module
