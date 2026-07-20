from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from tortoise import Tortoise, connections

from app.config import settings
from app.db import TORTOISE_ORM
from app.integrations.notion import post_daily

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await Tortoise.init(config=TORTOISE_ORM)
    # 영구 자동 데일리 리포터: NOTION_TOKEN 있을 때만 매일 18:03 등록.
    if settings.notion_token:
        scheduler.add_job(post_daily, "cron", hour=18, minute=3,
                          id="daily_notion", replace_existing=True)
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await connections.close_all()


app = FastAPI(title="SNS Keyword Monitor", lifespan=lifespan)


@app.get("/health")
async def health():
    # DB까지 실제로 왕복해야 "떠 있음"이 아니라 "동작함"을 증명한다.
    try:
        await connections.get("default").execute_query("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - health는 원인 문자열만 노출
        return {"status": "degraded", "db": f"error: {exc}"}
    return {"status": "ok", "db": "ok"}
