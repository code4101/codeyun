from __future__ import annotations

from fastapi import FastAPI

from .ai_chat import register as register_ai_chat_standard_feature
from .ai_config import register as register_ai_config_standard_feature
from .ai_evomind import register as register_ai_evomind_standard_feature
from .ai_git_commit import register as register_ai_git_commit_standard_feature
from .ai_notebook import register as register_ai_notebook_standard_feature
from .ai_reduction import register as register_ai_reduction_standard_feature
from .ai_wechat import register as register_ai_wechat_standard_feature


def register(app: FastAPI) -> None:
    register_ai_chat_standard_feature(app)
    register_ai_config_standard_feature(app)
    register_ai_evomind_standard_feature(app)
    register_ai_git_commit_standard_feature(app)
    register_ai_notebook_standard_feature(app)
    register_ai_reduction_standard_feature(app)
    register_ai_wechat_standard_feature(app)
