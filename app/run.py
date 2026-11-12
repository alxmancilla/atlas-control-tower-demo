from __future__ import annotations

import uvicorn

from .config import Settings, assert_safe_binding


def main() -> None:
    settings = Settings.from_env()
    assert_safe_binding(settings)
    uvicorn.run("app.main:create_app", factory=True, host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()