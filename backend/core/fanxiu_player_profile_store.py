"""Legacy compatibility alias for Fanxiu player-profile packet store helpers.

Canonical implementation lives in ``backend.core.fanxiu.player_profiles``.
"""

import sys

from backend.core.fanxiu import player_profiles as _module

sys.modules[__name__] = _module
