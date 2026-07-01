"""Legacy compatibility alias for Fanxiu Lua config helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.lua_config``.
"""

import sys

from backend.core.fanxiu.catalog import lua_config as _module

sys.modules[__name__] = _module
