# SNS 키워드 모니터링 · 사람 승인형 답글 도구

> **상태: 개발 완료 · 실사용 안 함** — Meta 앱 검수까지 통과했으나, 답글을 통한 유입 효과가 없다고 판단해 운영하지 않는다. 2인 팀(백엔드 정성운 / 프론트 진수연)
>
> 포트폴리오: [AX(AI 활용)](https://wjdtjddns98.github.io/) · [개발자](https://wjdtjddns98.github.io/dev/)

지정한 키워드가 들어간 SNS·커뮤니티 글을 모아 대시보드에 띄우고, **사람이 승인한 답글만** 보낸다. 자동 댓글 봇이 아니다 — 코드에 자동 발송 경로가 없다.

## 왜 만들었나 → 무엇이 됐나

- **문제** — 강아지 간식 관련 질문 글에 직접 답하면 쇼핑몰 유입이 생길 거라 봤다. 다만 자동 댓글은 플랫폼 제재·여론조작 리스크라 처음부터 배제했다.
- **결과** — 소스 어댑터(Threads · 네이버 카페 · RSS · 디시인사이드) → 키워드 매칭 → 승인 → 정확히 한 번 발송까지 만들고 Meta 앱 검수를 통과했다. 실제 운영해 보니 유입 효과가 없어 내렸다.

## 역할

- **정성운** — 백엔드 전담(FastAPI · 소스 어댑터 · 폴러/스케줄러 · 매칭 · 발송 · 암호화 · 마이그레이션 · 테스트) + Meta 앱 검수 대응.
- **진수연** — 프론트(`frontend/`).

## 핵심 설계 결정

1. **자동 발송 경로를 만들지 않는다** — 모든 전송은 approve 클릭 뒤에만. 제품 불변식이고 코드 리뷰 기준이다.
2. **이중 발송은 DB 가 막는다** — 승인은 CAS 클레임(`status IN ('new','reviewing') → 'sending'`, 0행이면 중단) + `reply_actions(matched_post_id) WHERE action='sent'` partial unique index. 앱 코드가 아니라 제약으로 "정확히 한 번"을 보장한다.
3. **토큰은 별도 테이블 + Fernet** — `MultiFernet` 으로 키 회전. API 응답·로그·URL 에 토큰이 나오지 않는다.
4. **예의 있는 수집** — 공식·공개 API 와 RSS 만, 헤드리스 스크레이핑 없음. 소스별 rate-limit 과 429/403 backoff.
5. **최소 아키텍처** — Postgres + in-process APScheduler(uvicorn 워커 1개). 브로커·캐시 없이 단일 서버에서 돈다.

## 아키텍처

폴러가 소스 어댑터를 주기 호출해 글을 모으고, 키워드 매칭 결과를 `matched_posts` 에 쌓는다. 대시보드(React)에서 사람이 템플릿 답글을 편집·승인하면 CAS 클레임을 거쳐 발송하고, 누가 언제 무엇을 승인·전송했는지 감사 로그가 남는다.

## 기술 스택

Python · FastAPI · Tortoise ORM + aerich · PostgreSQL · APScheduler · cryptography(Fernet) · React + TypeScript(Vite) · Docker Compose

## 실행

```bash
docker compose up        # db(호스트 5433) · api(8000) · frontend(5173)
```

백엔드 게이트: `ruff check .` + `pytest -q`. 프론트 게이트: `npm run build`.
설계 문서는 `docs/`(PRD · ERD · API-SPEC · 앱 검수 기록), 협업 규칙은 `CLAUDE.md`.
