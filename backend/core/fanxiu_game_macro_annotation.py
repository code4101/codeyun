"""Legacy compatibility alias for Fanxiu game macro annotation helpers.

Canonical implementation lives in ``backend.core.fanxiu.game.macro_annotation``.
"""

import sys

from backend.core.fanxiu.game import macro_annotation as _module

sys.modules[__name__] = _module
