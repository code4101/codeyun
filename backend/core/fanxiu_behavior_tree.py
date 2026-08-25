"""Legacy compatibility alias for the Fanxiu resident behavior tree.

Canonical implementation lives in ``backend.core.fanxiu.behavior_tree.runtime``.
Keep this module path importable so older call sites still work while future
maintenance targets the nested ``backend.core.fanxiu.*`` package tree.
"""

import sys

from backend.core.fanxiu.behavior_tree import runtime as _module

sys.modules[__name__] = _module
