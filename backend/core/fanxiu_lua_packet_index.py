"""Legacy compatibility alias for Fanxiu Lua packet index helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.lua_packet_index``.
"""

import sys

from backend.core.fanxiu.catalog import lua_packet_index as _module

sys.modules[__name__] = _module
