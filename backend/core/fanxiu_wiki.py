"""Legacy compatibility alias for Fanxiu wiki/resource text helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.wiki``.
"""

import sys

from backend.core.fanxiu.catalog import wiki as _module

sys.modules[__name__] = _module
