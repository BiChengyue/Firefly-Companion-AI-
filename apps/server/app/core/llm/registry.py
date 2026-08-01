"""LLM Provider 自动注册表 — 对应 spec 3.3.1。
新建提供商文件 app/core/llm/providers/my_new_ai.py 即可，无需改动本文件。
前端通过 GET /api/providers 自动发现已注册的 Provider。
"""
import importlib
import pkgutil

from app.core.llm.base import BaseLLMProvider


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
    """启动时动态导入内置 Provider 模块，触发 @register_provider 注册。"""
    try:
        from app.core.llm import providers as _providers_pkg
        for module_info in pkgutil.iter_modules(_providers_pkg.__path__):
            if module_info.name.startswith("_"):
                continue
            importlib.import_module(f"app.core.llm.providers.{module_info.name}")
    except ImportError:
        pass  # providers 包尚不存在
