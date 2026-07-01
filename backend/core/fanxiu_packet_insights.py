"""Legacy compatibility alias for Fanxiu packet insight helpers.

Canonical implementation lives in ``backend.core.fanxiu.packet.insights``.
"""

import sys

from backend.core.fanxiu.packet import insights as _module

sys.modules[__name__] = _module
