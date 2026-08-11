import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from tortoise import Tortoise, connections

from app import poller, reconcile, reply
from app.api.auth import router as auth_router
from app.api.keywords import router as keywords_router
from app.api.matches import router as matches_router
from app.api.sns_accounts import router as sns_accounts_router
from app.api.sources import router as sources_router
from app.api.templates import router as templates_router
from app.config import settings
from app.db import TORTOISE_ORM
from app.models import Source

scheduler = AsyncIOScheduler()

# httpx/httpcore 는 INFO 레벨에서 요청 URL 전체(쿼리 포함)를 로깅한다 — Threads OAuth
# 장기 토큰 전환은 공식 계약상 client_secret/access_token 이 쿼리로 나가므로, 앱을
# --log-level info/debug 로 띄워도 시크릿이 stdout 에 찍히지 않게 여기서 고정한다
# (불변식 ③, OAuth 1차 적대 리뷰 High-2 — httpx 0.28 실측).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.app_env == "prod":
        # prod fail-fast: 세션 키 필수, 자격증명 키는 설정돼 있으면 형식 검증(NFR-S1·S2).
        # 미설정/오형식이면 임시 키로 조용히 뜨는 대신 기동 자체를 실패시킨다.
        Fernet(settings.session_fernet_key)
        for key in filter(None, settings.credentials_fernet_keys.split(",")):
            Fernet(key.strip())
    await Tortoise.init(config=TORTOISE_ORM)
    # poller: 주기 수집 tick (FR-1). 소스별 poll_interval_sec 판정은 tick 안에서.
    if settings.poller_enabled:
        scheduler.add_job(poller.poll_tick, "interval",
                          seconds=settings.poller_tick_sec,
                          id="poller", replace_existing=True)
    # sending 정체 회수(PRD §7): approve 중 크래시로 고착된 매칭을 verify_pending/reviewing 으로.
    scheduler.add_job(reply.sweep_stuck_sending, "interval",
                      seconds=60, id="sending_sweep", replace_existing=True)
    # 전송 결과 불명 조정(M2 조정 설계 §3.4): read-only 조회로 게시 여부 확인 후 종결.
    scheduler.add_job(reconcile.reconcile_tick, "interval",
                      seconds=60, id="reconcile", replace_existing=True)
    if scheduler.get_jobs():
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
app.include_router(matches_router)


@app.get("/health")
async def health():
    # DB까지 실제로 왕복해야 "떠 있음"이 아니라 "동작함"을 증명한다.
    try:
        await connections.get("default").execute_query("SELECT 1")
        sources = await Source.filter(enabled=True).order_by("id")
    except Exception:  # noqa: BLE001 - 상세는 서버 로그에만(예외 문자열에 접속정보 포함 가능)
        logging.getLogger(__name__).exception("health: DB 왕복 실패")
        return {"status": "degraded", "db": "error"}
    last_tick = poller.state["last_tick"]
    # tick 이 돌다가 오래 멈췄으면 전체 상태에 반영(silent failure 방지).
    # 기동 직후(last_tick=None)는 첫 tick 전이므로 degraded 로 치지 않는다.
    poller_stale = (
        settings.poller_enabled
        and last_tick is not None
        and (datetime.now(UTC) - last_tick).total_seconds() > settings.poller_tick_sec * 5
    )
    return {
        "status": "degraded" if poller_stale else "ok",
        "db": "ok",
        # poller heartbeat + 소스별 상태 배지 (FR-17·18)
        "poller": {
            "last_tick": last_tick.isoformat() if last_tick else None,
            "sources": [
                {
                    "source_id": s.id,
                    "health_status": s.health_status.value,
                    "last_success_at": (
                        s.last_success_at.isoformat() if s.last_success_at else None
                    ),
                }
                for s in sources
            ],
        },
    }
