# PRD — SNS 키워드 모니터링 & 사람 승인형 답변 도구

> Status: **pending approval** · 작성 근거: ralplan 합의 루프(Planner→Architect→Critic) + 8개 MUST-FIX
> 최종 갱신: 2026-07-08

---

## 1. 개요 (Overview)

SNS/커뮤니티에서 **사용자가 지정한 키워드가 포함된 게시글을 지속 감지**하고, 감지된 글을 대시보드에 모아 **사람이 검토·승인한 경우에만** 미리 준비한 답변을 전송하는 소셜 리스닝 웹앱.

**핵심 성격:** 자동 대량 댓글 봇이 **아니다**. 모든 전송은 사람의 명시적 승인(버튼 클릭)을 거친다. 이는 플랫폼 ToS 준수·스팸/여론조작 방지·법적 리스크 회피를 위한 **불변 제품 제약**이다.

---

## 2. 목표 / 비목표 (Goals / Non-Goals)

### Goals
- G1. 여러 소스(Threads, 네이버 카페, 일반 커뮤니티)에서 키워드 매칭 글을 **예의 있게**(rate-limit 준수) 수집한다.
- G2. 감지된 글을 대시보드에서 검토하고, 템플릿 기반 답변을 편집·승인한다.
- G3. 승인된 답변을 **정확히 1번** 전송한다(이중 발송 구조적 차단).
- G4. 누가·언제·무엇을 승인/전송했는지 불변 감사 로그로 남긴다.
- G5. 단일 개발자가 단일 서버에서 저비용으로 운영한다.

### Non-Goals (명시적 범위 밖)
- N1. **자동 게시** (사람 개입 없는 전송) — 영구 미지원.
- N2. 네이버 카페/커뮤니티 **자동 write** — API 부재/ToS로 미지원. 감지 + 수동 복사(clipboard)만.
- N3. 멀티테넌시/SaaS 과금 — YAGNI.
- N4. 대규모 분산 크롤링·스크레이핑 — ToS 위반 리스크로 배제.
- N5. 실시간(초 단위) 감지 — 폴링 기반 분 단위로 충분.

---

## 3. 사용자 & 역할 (Personas / Roles)

- **admin**: 소스·SNS 계정·키워드·템플릿·사용자 관리.
- **reviewer**: 매칭 글 검토, 답변 승인·전송.

초기엔 소수 내부 사용자. 인증은 이메일+비밀번호(argon2) + HttpOnly 세션 쿠키.

---

## 4. 소스 능력 매트릭스 (Source Capability Matrix)

| 소스 | 수집(read) | 전송(write) | 수집 방식 |
|---|---|---|---|
| **Threads (Meta)** | ✅ | ✅ *(Meta 앱 심사 승인 시)* | Threads/Graph API |
| **네이버 카페** | ✅ | ❌ → 수동 복사 | 네이버 검색 OpenAPI + 카페 RSS |
| **일반 커뮤니티** | ✅ | ❌ → 수동 복사 | 공개 RSS / 공개 검색 |

> `can_write=False` 소스: "승인" 시 최종 문구를 **클립보드 복사용**으로 제공(사람이 외부에서 직접 붙여넣기). 이는 임시 fallback이 아니라 상시 first-class 경로.

---

## 5. 기능 요구사항 (Functional Requirements)

### 수집 (Ingestion)
- **FR-1** 시스템은 소스별 `poll_interval_sec` 주기로 어댑터 `fetch()`를 호출해 신규 글을 수집한다.
- **FR-2** 각 글은 `(source_id, external_post_id)`로 유일 식별되며 중복 수집은 무시(upsert-ignore)한다.
- **FR-3** `external_post_id`는 재폴링·표면적 URL 변형(쿼리스트링/http↔https)에도 **안정적**이어야 한다. 해시 입력의 timestamp는 소스의 **canonical 게시 절대시각**을 쓰고, 게시시각을 노출하지 않는 소스는 timestamp를 제외(URL+author). *(MUST-FIX #2)*
- **FR-4** 429/403 응답 시 소스별 지수 backoff(`backoff_until`)를 적용하고 `health_status`를 갱신한다.
- **FR-5** 소스 복구 시 `last_success_at` 커서 이후 구간을 백필한다. 커서는 **store 성공 후에만** 전진한다. 복구는 소스 보존창 내 **best-effort**(장기 다운타임은 복구 불가 — hard ceiling 명시). *(MUST-FIX #5)*

### 매칭 (Matching)
- **FR-6** 키워드는 `substring` 또는 `regex` 매칭(MVP). 한국어 형태소(Kiwi)는 오탐/누락이 실측 문제일 때만 도입(`ponytail:` 주석으로 upgrade 경로 명시, CPU-bound라 `ProcessPoolExecutor` 필요).
- **FR-7** 매칭된 글은 `matched_posts`에 `status=new`로 저장된다.

### 검토 & 전송 (Review & Send)
- **FR-8** reviewer는 대시보드에서 매칭 글을 필터(status/source)로 조회한다.
- **FR-9** reviewer는 템플릿을 선택·편집해 최종 답변 문구를 만든다.
- **FR-10** "승인" 시 시스템은 **CAS 클레임**으로 글을 선점한다: `status IN ('new','reviewing') → 'sending'`. 0행이면 이미 다른 요청이 선점 → abort(이중 발송 방지). *(MUST-FIX #1)*
- **FR-11** 선점 성공 후 어댑터로 전송하고, 결과를 `reply_actions`에 기록: 성공 `sent`(+external_reply_id), 실패 `failed`(+error).
- **FR-12** `reply_actions`는 matched_post당 `action='sent'` 최대 1건을 **DB partial unique index**로 강제한다. *(MUST-FIX #1)*
- **FR-13** 전송 실패는 **자동 재시도하지 않는다**(스팸 방지). reviewer의 수동 재시도만 허용, 재시도는 새 `reply_actions` 행.
- **FR-14** 전송 결과 불명(unknown outcome) 처리 — 상세 설계는 `docs/M2-SEND-RECONCILIATION.md`. *(MUST-FIX #4, OQ-1 닫힘 2026-07-21: Threads idempotency-key 미지원 확정)*
  - 확정 실패(게시 안 됐음이 보장)만 `reviewing` 복귀로 retry 를 연다. publish 단계의 타임아웃/응답유실/**5xx**는 **결과 불명** → `verify_pending` 전이(재전송 구조 차단, ignore 만 허용).
  - **조정(reconciliation) 잡**이 대상 글의 답글을 read-only 조회해 실제 게시 여부를 판정: 발견 → `sent` 회계+`replied`, 미발견 상한 도달 → 미게시 판정+`reviewing`(retry 재개, "직접 확인 권장" 안내).
  - `sending` 정체 행(프로세스 crash 등)은 sweep 이 회수하되, 전송 착수분(verify_meta 有)은 `verify_pending` 으로 — 죽은 클레임도 결과 불명이다.
- **FR-15** `can_write=False` 소스는 전송 대신 클립보드 복사 문구를 제공하고 `reply_actions`에 `approved`만 기록.

### 감사 & 관측 (Audit & Observability)
- **FR-16** `reply_actions`는 append-only(누가/언제/무엇/결과).
- **FR-17** `/health`는 API + DB 왕복 + poller heartbeat를 반영한다.
- **FR-18** 대시보드는 소스별 `last_success_at`·`health_status` 배지로 silent failure를 가시화한다.

### 관리 (Admin CRUD)
- **FR-19** 소스/키워드/템플릿/SNS 계정/사용자 CRUD. SNS 계정 응답에는 **자격증명이 절대 포함되지 않는다**. *(MUST-FIX #3)*

---

## 6. 비기능 요구사항 (Non-Functional)

### 보안 (Security) — driver 최상위 등급
- **NFR-S1** SNS 자격증명/토큰은 at-rest 암호화(Fernet). 암호문은 **별도 테이블**(`sns_account_secrets`)에 두어 read path가 물리적으로 로드하지 않는다. 응답 스키마는 ORM 모델이 아닌 전용 read schema. *(MUST-FIX #3)*
- **NFR-S2** 키 회전: `MultiFernet([new, old])` 경로를 열어둔다(1줄).
- **NFR-S3** 토큰은 로그·트레이스백·URL 쿼리스트링에 절대 노출 금지. prod에서 httpx DEBUG 로깅 금지, redaction 필터 적용.
- **NFR-S4** `approve` 등 write 엔드포인트는 CSRF 방어(세션쿠키 + SameSite=Lax + CSRF 토큰). *(MUST-FIX #6, MUST 등급)*
- **NFR-S5** 위협모델 문서화. **명시:** Fernet은 *백업/DB덤프 유출* 방어이지 *호스트 침해* 방어가 아니다(과대선전 금지).

### 컴플라이언스 (Compliance)
- **NFR-C1** 공식/공개 수집 경로만 사용. Headless 스크레이핑 미채택.
- **NFR-C2** 자동 게시 경로 코드상 부재.

### 성능/운영 (Performance / Ops)
- **NFR-P1** uvicorn `--workers 1` 고정(in-process APScheduler 중복 실행 방지). CPU 작업은 executor로 offload. *(MUST-FIX #4)*
- **NFR-P2** 단일 VPS + docker-compose(caddy/api/db). Caddy 자동 TLS.
- **NFR-P3** Postgres 백업: `pg_dump` 일 1회 + **off-host** 복사. 배포 시 마이그레이션 직전 pre-migration 스냅샷.
- **NFR-P4** 마이그레이션 aerich, forward-only 규율(destructive 전 deprecate). rollback = pre-migration 스냅샷 복원.
- **NFR-P5** 구조적 JSON 로깅.

---

## 7. 핵심 플로우 (Key Flows)

**수집:** `poller(주기) → adapter.fetch(since=last_success_at) → 키워드 매칭 → matched_posts(new) upsert(dedup) → last_success_at 전진(store 성공 시)`

**전송:** `대시보드에서 reviewer 검토·편집 → 승인 클릭 → CAS: new/reviewing→sending → adapter.send_reply → sent(external_reply_id) | failed(error) → audit 기록`
정체 시: `sending(>N분) → sweep → reviewing`

---

## 8. 마일스톤 & 수용 기준 (runnable green)

| 단계 | 범위 | 수용 기준 (검증 가능) |
|---|---|---|
| **M0** ✅ | 하네스 | `docker compose up`→`/health` 200 + DB 왕복 *(완료·검증됨)* |
| **M1** | 모델7종·auth·수집·대시보드·승인 | 로그인→소스등록→매칭 대시보드 표시→승인→(mock)send→`reply_actions` sent 1건 audit, **e2e green** + 테스트게이트(아래) green |
| **M2** | Threads write·견고성 | 429주입→`backoff_until`+`health='degraded'` 뱃지, **동시 approve 2건→send 1건** green, Threads 실발송→external_reply_id 기록. **선결(승인 2차 리뷰 H-1)**: write 어댑터 착수 전 provider idempotency-key(OQ-1) 확보 또는 unknown-outcome(타임아웃 후 실제 게시 여부 불명) 조회·조정 절차 설계 — 타임아웃 취소는 외부 사이드이펙트 중단을 보장하지 않는다 |
| **v1** | 운영화 | Caddy TLS 배포, pg_dump **off-host** 백업→신규 DB 복원 성공, admin/reviewer 권한매트릭스 green, 위협모델 문서 존재 |

### 테스트 게이트 5종 (M1 필수) *(MUST-FIX #7)*
1. 동시 `approve` 2건 → 정확히 1건 `action='sent'`, 나머지 CAS 0-row abort.
2. `/sns-accounts` 응답 JSON에 암호문/토큰 substring **부재** assert.
3. 소스 다운 후 `last_success_at` 이전 window의 mention **복구** assert(store 성공 후 커서 전진 포함).
4. uvicorn `--workers 1` config assert.
5. 동일 논리 글을 URL/쿼리스트링만 바꿔 2회 투입 → **동일 external_post_id** assert.

---

## 9. 리스크 & Pre-mortem

| 시나리오 | 완화 |
|---|---|
| Threads 앱 심사 반려 | read/clipboard degrade, 제품 생존; M0 선착수로 리드타임 확보 |
| 자격증명 유출 | 별도 테이블+Fernet+redaction+응답 배제(NFR-S1~3) |
| rate-limit ban | polite interval + backoff + health 배지 |
| **동일 글 이중 댓글 → 스팸신고/차단** | CAS + partial unique + external_id 안정성(MUST-FIX #1,#2) |
| **다운타임 중 mention 영구 유실** | last_success_at 커서 백필, 보존창 ceiling 명시(MUST-FIX #5) |

---

## 10. Open Questions (개발 전 확인)
- ~~OQ-1. Threads/Graph API가 게시에 **idempotency-key**를 지원하는가? (FR-14 최선책 성립 여부 — M0/M1 조사)~~
  **→ 닫힘(2026-07-21): 미지원.** unknown-outcome 조회·조정 절차로 대체 — 설계는 `docs/M2-SEND-RECONCILIATION.md` (M2 선결조건 이행).
- OQ-2. Meta 앱 심사 리드타임/요건? (M2 write 일정에 영향)
- OQ-3. 대상 네이버 카페의 RSS/검색 노출 범위(본문 접근 한계)?

---

## 11. 확정 스택
FastAPI · uvicorn(workers=1) · Postgres · Tortoise ORM(aerich) · React+TypeScript · Docker(compose) · Caddy(자동 TLS) · 단일 VPS 배포.
