"""Legacy compatibility alias for Fanxiu item catalog helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.item``.
"""

import sys

from backend.core.fanxiu.catalog import item as _module

sys.modules[__name__] = _module
