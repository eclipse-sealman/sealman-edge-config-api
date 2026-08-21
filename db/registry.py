from typing import Type, TypeVar, Dict, cast

T = TypeVar("T")


class RepositoryRegistry:
    def __init__(self):
        self._repos: Dict[Type, Type] = {}

    def register(self, interface: Type[T], impl: Type[T]) -> None:
        if not issubclass(impl, interface):
            raise TypeError(f"{impl.__name__} must implement {interface.__name__}")

        if interface in self._repos:
            raise RuntimeError(
                f"Repository already registered for {interface.__name__}"
            )

        self._repos[interface] = impl

    def get(self, interface: Type[T]) -> Type[T]:
        if interface not in self._repos:
            raise RuntimeError(f"No repository registered for {interface.__name__}")
        return cast(Type[T], self._repos[interface])


repo_registry = RepositoryRegistry()


def register_repository(interface: Type):
    def decorator(cls):
        repo_registry.register(interface, cls)
        return cls

    return decorator

