"""Legacy compatibility alias for Fanxiu gongfa catalog helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.gongfa``.
"""

import sys

from backend.core.fanxiu.catalog import gongfa as _module

sys.modules[__name__] = _module
