"""Opaque wrapper for credential strings inside application code."""

from typing import Any, NoReturn, SupportsIndex, final


@final
class SecretToken:
    """Mask incidental rendering and require an explicit credential handoff."""

    __slots__ = ("__value",)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("SecretToken cannot be subclassed")

    def __init__(self, value: str) -> None:
        if type(value) is not str:
            raise TypeError("SecretToken requires a plain string")
        self.__value = value

    def reveal(self) -> str:
        """Return plaintext for an authorized credential boundary only."""
        return self.__value

    def __str__(self) -> str:
        return "***"

    __repr__ = __str__

    def __format__(self, format_spec: str) -> str:
        return "***"

    def __getstate__(self) -> NoReturn:
        raise TypeError("SecretToken cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("SecretToken cannot be serialized")
