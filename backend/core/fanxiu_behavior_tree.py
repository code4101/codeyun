"""Legacy compatibility alias for the Fanxiu resident behavior tree.

Canonical implementation lives in ``backend.core.fanxiu.runtime.behavior_tree``.
Keep this module path importable so older call sites still work while future
maintenance targets the nested ``backend.core.fanxiu.*`` package tree.
"""

import sys

from backend.core.fanxiu.runtime import behavior_tree as _module

sys.modules[__name__] = _module
