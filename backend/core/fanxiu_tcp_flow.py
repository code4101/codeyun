"""Legacy compatibility alias for Fanxiu TCP flow helpers.

Canonical implementation lives in ``backend.core.fanxiu.packet.tcp_flow``.
"""

import sys

from backend.core.fanxiu.packet import tcp_flow as _module

sys.modules[__name__] = _module
