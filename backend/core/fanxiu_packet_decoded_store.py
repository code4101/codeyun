"""Legacy compatibility alias for Fanxiu packet decoded-store helpers.

Canonical implementation lives in ``backend.core.fanxiu.packet.decoded_store``.
"""

import sys

from backend.core.fanxiu.packet import decoded_store as _module

sys.modules[__name__] = _module
