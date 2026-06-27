import sys

from backend.core.runtime import system_metrics as _module

sys.modules[__name__] = _module
