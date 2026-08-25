"""Legacy compatibility alias for Fanxiu data-annotation runtime control.

Canonical implementation lives in
``backend.core.fanxiu.data_annotation.behavior_tree_control``.
"""

import sys

from backend.core.fanxiu.data_annotation import behavior_tree_control as _module

sys.modules[__name__] = _module
