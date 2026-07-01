"""Legacy compatibility alias for Fanxiu packet activity helpers.

Canonical implementation lives in ``backend.core.fanxiu.packet.activity``.
"""

import sys

from backend.core.fanxiu.packet import activity as _module

sys.modules[__name__] = _module
