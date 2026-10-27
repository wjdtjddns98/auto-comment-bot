# SNS 키워드 모니터링 · 사람 승인형 답글 도구

지정한 키워드가 들어간 SNS·커뮤니티 글을 모아 대시보드에 띄우고, **사람이 승인한 답글만** 보낸다. 자동 댓글 봇이 아니다 — 코드에 자동 발송 경로가 없다. Meta 앱 검수를 통과했고, 현재는 운영하지 않는다.

## 아키텍처

폴러가 소스 어댑터(Threads · 네이버 카페 · RSS · 디시인사이드)를 주기 호출해 글을 모으고, 키워드 매칭 결과를 `matched_posts` 에 쌓는다. 대시보드(React)에서 사람이 템플릿 답글을 편집·승인하면 CAS 클레임을 거쳐 정확히 한 번 발송하고, 누가 언제 무엇을 승인·전송했는지 감사 로그가 남는다. 소스 토큰은 별도 테이블에 Fernet 으로 암호화해 둔다.

Postgres + in-process APScheduler(uvicorn 워커 1개) 구성으로 브로커·캐시 없이 단일 서버에서 돈다. 수집은 공식·공개 API 와 RSS 만 쓰고, 소스별 rate-limit 과 429/403 backoff 를 둔다.

## 기술 스택

Python · FastAPI · Tortoise ORM + aerich · PostgreSQL · APScheduler · cryptography(Fernet) · React + TypeScript(Vite) · Docker Compose

## 실행

```bash
docker compose up        # db(호스트 5433) · api(8000) · frontend(5173)
```

백엔드 게이트: `ruff check .` + `pytest -q`. 프론트 게이트: `npm run build`.
설계 문서는 `docs/`(PRD · ERD · API-SPEC · 앱 검수 기록), 협업 규칙은 `CLAUDE.md`.
