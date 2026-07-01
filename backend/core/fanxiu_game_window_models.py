"""Legacy compatibility alias for Fanxiu game-window response models.

Canonical implementation lives in ``backend.core.fanxiu.game.window_models``.
"""

import sys

from backend.core.fanxiu.game import window_models as _module

sys.modules[__name__] = _module
