"""Legacy compatibility alias for Fanxiu packet capture helpers.

Canonical implementation lives in ``backend.core.fanxiu.packet.capture``.
"""

import sys

from backend.core.fanxiu.packet import capture as _module

sys.modules[__name__] = _module
