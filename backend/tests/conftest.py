"""테스트 공용 픽스처.

- `db` 마커: 실 Postgres 필요. DATABASE_URL 미설정이면 자동 스킵(CI `test` 잡은 DB 없이 green,
  `pg-migration` 잡이 aerich 적용 후 `pytest -m db` 로 실행).
- api_client: ASGI 인프로세스 클라이언트 + Tortoise 초기화(스케줄러는 안 띄움).
"""
import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="DATABASE_URL 미설정 — DB 통합 테스트 스킵")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient
    from tortoise import Tortoise

    from app.db import TORTOISE_ORM
    from app.main import app

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        await Tortoise.close_connections()
