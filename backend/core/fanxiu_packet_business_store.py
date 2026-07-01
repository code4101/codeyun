"""Legacy compatibility alias for Fanxiu packet business store helpers.

Canonical implementation lives in ``backend.core.fanxiu.packet.business_store``.
"""

import sys

from backend.core.fanxiu.packet import business_store as _module

sys.modules[__name__] = _module
