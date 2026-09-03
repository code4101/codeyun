"""Compatibility alias for the Fanxiu visual macro executor.

Canonical implementation lives in ``backend.core.fanxiu.game.visual_macro_executor``.
"""

import sys

from backend.core.fanxiu.game import visual_macro_executor as _module

sys.modules[__name__] = _module
