"""Web 面板适配层（框架相关：会导入 ``astrbot.api.web``）。"""

from .api import PLUGIN_NAME, register_web_apis

__all__ = ["register_web_apis", "PLUGIN_NAME"]
