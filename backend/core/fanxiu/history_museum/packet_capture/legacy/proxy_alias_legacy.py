"""Legacy compatibility alias for Fanxiu packet proxy helpers.

Canonical implementation lives in ``backend.core.fanxiu.packet.proxy``.
"""

import sys

from backend.core.fanxiu.packet import proxy as _module

sys.modules[__name__] = _module
