"""Legacy compatibility alias for Fanxiu game-window action helpers.

Canonical implementation lives in ``backend.core.fanxiu.game.window_actions``.
"""

import sys

from backend.core.fanxiu.game import window_actions as _module

sys.modules[__name__] = _module
