import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from tortoise import Tortoise, connections

from app.api.auth import router as auth_router
from app.api.keywords import router as keywords_router
from app.api.sns_accounts import router as sns_accounts_router
from app.api.sources import router as sources_router
from app.api.templates import router as templates_router
from app.config import settings
from app.db import TORTOISE_ORM
from app.integrations.notion import post_daily

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.app_env == "prod":
        # prod fail-fast: 세션 키 필수, 자격증명 키는 설정돼 있으면 형식 검증(NFR-S1·S2).
        # 미설정/오형식이면 임시 키로 조용히 뜨는 대신 기동 자체를 실패시킨다.
        Fernet(settings.session_fernet_key)
        for key in filter(None, settings.credentials_fernet_keys.split(",")):
            Fernet(key.strip())
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


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request, exc: RequestValidationError) -> JSONResponse:
    # 기본 핸들러는 입력 원문을 detail[].input 으로 echo 한다 — 자격증명 등 민감 입력이
    # 응답·프록시 로그·에러 트래커로 재노출되는 경로라 제거한다(불변식 ③).
    errors = [
        {k: v for k, v in err.items() if k not in ("input", "ctx")} for err in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})
app.include_router(auth_router)
app.include_router(sources_router)
app.include_router(keywords_router)
app.include_router(templates_router)
app.include_router(sns_accounts_router)


@app.get("/health")
async def health():
    # DB까지 실제로 왕복해야 "떠 있음"이 아니라 "동작함"을 증명한다.
    try:
        await connections.get("default").execute_query("SELECT 1")
    except Exception:  # noqa: BLE001 - 상세는 서버 로그에만(예외 문자열에 접속정보 포함 가능)
        logging.getLogger(__name__).exception("health: DB 왕복 실패")
        return {"status": "degraded", "db": "error"}
    return {"status": "ok", "db": "ok"}
