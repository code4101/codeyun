"""Legacy compatibility alias for Fanxiu hot-update analysis helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.hot_update``.
"""

import sys

from backend.core.fanxiu.catalog import hot_update as _module

sys.modules[__name__] = _module
