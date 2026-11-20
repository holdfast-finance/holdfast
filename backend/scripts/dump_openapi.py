"""Print the shipping OpenAPI contract. See ``app.openapi_export``."""

from __future__ import annotations

from app.openapi_export import openapi_text


def main() -> None:
    print(openapi_text(), end="")


if __name__ == "__main__":
    main()
