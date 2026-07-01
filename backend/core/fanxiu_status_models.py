"""Legacy compatibility alias for Fanxiu status models.

Canonical schemas live in ``backend.core.fanxiu.catalog.status_models``.
"""

import sys

from backend.core.fanxiu.catalog import status_models as _module

sys.modules[__name__] = _module
