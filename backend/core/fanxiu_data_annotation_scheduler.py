"""Legacy compatibility alias for Fanxiu data-annotation scheduler helpers.

Canonical implementation lives in ``backend.core.fanxiu.data_annotation.scheduler``.
"""

import sys

from backend.core.fanxiu.data_annotation import scheduler as _module

sys.modules[__name__] = _module
