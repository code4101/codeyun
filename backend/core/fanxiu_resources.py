"""Legacy compatibility alias for Fanxiu resource helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.resources``.
"""

import sys

from backend.core.fanxiu.catalog import resources as _module

sys.modules[__name__] = _module
