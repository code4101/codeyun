"""Compatibility alias for the Fanxiu Kernel scheduler implementation.

Canonical implementation lives in ``backend.core.fanxiu.behavior_tree.kernel_scheduler``.
Keep this historical top-level module importable while maintenance targets the
nested ``backend.core.fanxiu.*`` package tree.
"""

import sys

from backend.core.fanxiu.behavior_tree import kernel_scheduler as _module

sys.modules[__name__] = _module
