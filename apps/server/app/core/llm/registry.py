"""LLM Provider 自动注册表 — 对应 spec 3.3.1。
新建提供商文件 app/core/llm/providers/my_new_ai.py 即可，无需改动本文件。
前端通过 GET /api/providers 自动发现已注册的 Provider。
"""
import importlib
import logging
import pkgutil

from app.core.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class LLMProviderRegistry:
    """Provider 注册表。"""

    _providers: dict[str, type[BaseLLMProvider]] = {}

    @classmethod
    def register(cls, name: str):
        """装饰器：注册一个 LLM Provider。

        Example:
            @register_provider("my_new_ai")
            class MyNewAIProvider(BaseLLMProvider):
                ...
        """
        def decorator(provider_cls: type[BaseLLMProvider]):
            provider_cls.provider_name = name
            cls._providers[name] = provider_cls
            return provider_cls
        return decorator

    @classmethod
    def get(cls, name: str) -> type[BaseLLMProvider]:
        if name not in cls._providers:
            raise KeyError(
                f"LLM Provider '{name}' 未注册。已注册: {list(cls._providers.keys())}"
            )
        return cls._providers[name]

    @classmethod
    def list_providers(cls) -> list[str]:
        return list(cls._providers.keys())

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseLLMProvider:
        """创建 Provider 实例。"""
        return cls.get(name)(**kwargs)


# 全局便捷别名
register_provider = LLMProviderRegistry.register


def load_builtin_providers():
    """启动时动态导入内置 Provider 模块，触发 @register_provider 注册。

    注意：PyInstaller 打包后 pkgutil.iter_modules() 无法枚举 PYZ 归档内的模块，
    因此先显式导入内置 Provider 模块列表，再回退到目录扫描（开发模式）。
    """
    # 显式列出的内置 Provider 模块（PyInstaller 下保证能被注册）
    _BUILTIN_PROVIDER_MODULES = [
        "app.core.llm.providers.openai_compat",
    ]
    for _mod in _BUILTIN_PROVIDER_MODULES:
        try:
            importlib.import_module(_mod)
        except ImportError:
            pass

    # 回退：目录扫描（开发模式，新增 Provider 文件自动发现）
    try:
        from app.core.llm import providers as _providers_pkg
        for module_info in pkgutil.iter_modules(_providers_pkg.__path__):
            if module_info.name.startswith("_"):
                continue
            module_path = f"app.core.llm.providers.{module_info.name}"
            if module_path not in _BUILTIN_PROVIDER_MODULES:
                importlib.import_module(module_path)
    except ImportError:
        pass  # providers 包尚不存在

    # 诊断日志：确认注册结果（PyInstaller 下 pkgutil 扫描失效，借此确认显式导入生效）
    logger.info("LLM Provider 注册完成: %s", list(LLMProviderRegistry._providers.keys()))
