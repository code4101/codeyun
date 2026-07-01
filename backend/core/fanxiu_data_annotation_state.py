"""Legacy compatibility alias for Fanxiu data-annotation runtime state helpers.

Canonical implementation lives in ``backend.core.fanxiu.data_annotation.state``.
"""

import sys

from backend.core.fanxiu.data_annotation import state as _module

sys.modules[__name__] = _module
