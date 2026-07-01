"""Legacy compatibility alias for Fanxiu debug-eval runtime jobs.

Canonical implementation lives in ``backend.core.fanxiu.data_annotation.debug_eval``.
"""

import sys

from backend.core.fanxiu.data_annotation import debug_eval as _module

sys.modules[__name__] = _module
