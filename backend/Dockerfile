# python:3.14 휠 생태계가 아직 미성숙(asyncpg 등) → 안정적인 3.12 slim 고정
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app

# git: 영구 자동 리포터가 /repo 의 커밋 로그를 읽는다
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# dev 컴포즈가 ./backend 를 /app 에 바인드 마운트하므로 venv 는 /app 밖에 둔다
# (마운트가 이미지 내 /app/.venv 를 가리는 것 방지)
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# 의존성 레이어 캐시: 락파일 먼저 복사
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

ENV PATH="/opt/venv/bin:$PATH"

COPY . .

EXPOSE 8000

# ponytail: reload 없이 단일 worker. 스케줄러가 붙는 M1 이후에도 workers=1 유지
#           (in-process APScheduler 중복 실행 방지 — Architect MUST-FIX #4).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
