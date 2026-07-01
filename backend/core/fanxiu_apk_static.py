"""Legacy compatibility alias for Fanxiu APK/static asset helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.apk_static``.
"""

import sys

from backend.core.fanxiu.catalog import apk_static as _module

sys.modules[__name__] = _module
