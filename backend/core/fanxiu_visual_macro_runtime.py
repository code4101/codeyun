"""Legacy compatibility alias for Fanxiu visual macro runtime helpers.

Canonical implementation lives in ``backend.core.fanxiu.game.visual_macro_runtime``.
"""

import sys

from backend.core.fanxiu.game import visual_macro_runtime as _module

sys.modules[__name__] = _module
