# python:3.14 휠 생태계가 아직 미성숙(asyncpg 등) → 안정적인 3.12 slim 고정
FROM python:3.12-slim

WORKDIR /app

# git: 영구 자동 리포터가 /repo 의 커밋 로그를 읽는다
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# 의존성 레이어 캐시: requirements 먼저 복사
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# ponytail: reload 없이 단일 worker. 스케줄러가 붙는 M1 이후에도 workers=1 유지
#           (in-process APScheduler 중복 실행 방지 — Architect MUST-FIX #4).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
