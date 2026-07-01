"""Legacy compatibility alias for Fanxiu packet insight worker helpers.

Canonical implementation lives in ``backend.core.fanxiu.packet.insight_worker``.
"""

import sys

from backend.core.fanxiu.packet import insight_worker as _module

sys.modules[__name__] = _module
