"""Legacy compatibility alias for Fanxiu player-profile packet store helpers.

Canonical implementation lives in ``backend.core.fanxiu.packet.player_profile_store``.
"""

import sys

from backend.core.fanxiu.packet import player_profile_store as _module

sys.modules[__name__] = _module
