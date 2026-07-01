"""Legacy compatibility alias for Fanxiu timeline helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.timeline``.
"""

import sys

from backend.core.fanxiu.catalog import timeline as _module

sys.modules[__name__] = _module
