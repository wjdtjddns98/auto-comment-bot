# API 명세서

> FastAPI REST. PRD/ERD 기반. Base URL `/api`. JSON.
> 인증: HttpOnly 세션 쿠키. write 요청은 CSRF 토큰 필요(NFR-S4).
> 최종 갱신: 2026-07-08 · Status: pending approval

---

## 공통

**인증:** 로그인 시 세션 쿠키(`HttpOnly`, `Secure`, `SameSite=Lax`) 발급.
**CSRF:** 상태변경(POST/PATCH/DELETE)은 `X-CSRF-Token` 헤더 필수(GET `/api/auth/csrf`로 발급). *(approve 포함 — MUST)*
**권한:** `admin`=관리 CRUD 전부, `reviewer`=매칭 조회/승인·전송.
**에러 포맷:** `{ "detail": "message" }`, 표준 HTTP 코드(400/401/403/404/409/422).
**⚠️ 토큰 배제:** SNS 계정 관련 어떤 응답에도 `encrypted_credentials`/원문 토큰이 **포함되지 않는다**(MUST-FIX #3).

---

## 인증

### `POST /api/auth/login`
Req `{ "email": "...", "password": "..." }` → 200 + 세션쿠키 `{ "id", "email", "role" }` · 401 실패.

### `POST /api/auth/logout`
200. 세션 무효화.

### `GET /api/auth/me`
200 `{ "id", "email", "role" }` · 401.

### `GET /api/auth/csrf`
200 `{ "csrf_token": "..." }`.

---

## 소스 (admin)

### `GET /api/sources`
200 `[{ id, type, config, poll_interval_sec, enabled, last_success_at, health_status, backoff_until }]`

### `POST /api/sources`
Req `{ type: "threads|naver_cafe|community", config: {...}, poll_interval_sec }` → 201 source.

> `config` 는 **타입별 스키마로 검증**된다(등록·수정 시점 422, 미지의 키 불허):
> - `community`: `{ "rss_url": "https://..." }` (http/https URL 필수)
> - `threads` · `naver_cafe`: 어댑터 미구현 — 등록 자체가 422 (M2 예정)
>
> 검증 실패 시 `422 { detail: "소스 설정이 올바르지 않습니다 — config.<필드>: <사유>[; ...]" }`.
> URL 은 표준형으로 정규화되어 저장·응답될 수 있다(예: `https://ex.am` → `https://ex.am/`).
> userinfo 포함 URL(`https://user:pw@...`)은 422 (자격증명이 config 로 저장되는 것 차단).

### `PATCH /api/sources/{id}`
Req(부분) `{ enabled?, poll_interval_sec?, config? }` → 200. `config` 는 위 타입별 스키마로 검증(422).
`enabled: true` 재활성화 시에는 저장된 config 도 재검증한다 — 무효 config 소스를 그대로 켤 수 없다(422).

### `DELETE /api/sources/{id}` → 204.

---

## 키워드 (admin)

### `GET /api/keywords` → 200 `[{ id, pattern, match_type, enabled, source_scope }]`
### `POST /api/keywords`
Req `{ pattern, match_type: "substring|regex", source_scope?: int|null }` → 201.
422: regex 컴파일 실패 시.
### `PATCH /api/keywords/{id}` · `DELETE /api/keywords/{id}`

---

## 템플릿 (admin)

### `GET /api/templates` → 200 `[{ id, name, body, enabled }]`
### `POST /api/templates` Req `{ name, body }` → 201.
### `PATCH /api/templates/{id}` · `DELETE /api/templates/{id}`

---

## SNS 계정 (로그인 사용자 — 본인 귀속 셀프서비스) — 토큰 배제

> 정책: 사용자는 **자기 SNS 계정만** 연동/조회/삭제한다. admin 은 전체 조회·삭제 가능(운영용).
> M2 에서 Threads OAuth 콜백(`/api/sns-accounts/threads/oauth-url`·callback)이 추가될 예정 —
> 그때까지는 개발자 콘솔에서 발급한 토큰을 credentials 로 직접 등록한다.

### `GET /api/sns-accounts`
200 `[{ id, user_id, platform, display_name, status, token_expires_at }]` — **암호문/토큰 필드 없음**.
본인 계정만 반환(admin 은 전체).

### `POST /api/sns-accounts`
Req `{ platform, display_name, credentials: {...} }` → 201 `{ id, user_id, platform, display_name, status, token_expires_at }`.
→ `credentials`는 서버가 즉시 Fernet 암호화해 `sns_account_secrets`에 저장. 응답에 재노출 안 함.
→ 생성 주체에게 자동 귀속(타인 명의 등록 불가).

### `DELETE /api/sns-accounts/{id}` → 204 (secrets cascade). 본인 것만(admin 은 전체) · 타인 것은 404.

---

## 매칭 (reviewer)

### `GET /api/matches`
Query: `status`(new|reviewing|sending|replied|ignored), `source_id`, `page`, `size`.
200 `{ items: [{ id, source_id, external_post_id, author, url, content, matched_keyword_id, published_at, matched_at, status }], total }`

### `GET /api/matches/{id}`
200 매칭 상세 + `reply_actions` 이력 요약.

### `POST /api/matches/{id}/approve` — 핵심 (CSRF 필수)
Req `{ template_id?: int, final_body: string, sns_account_id?: int }`
동작(MUST-FIX #1):
1. CAS: `status IN('new','reviewing') → 'sending'`. **0행이면 409 Conflict**(이미 처리 중/완료).
2. 소스 `can_write=true`: `adapter.send_reply` → 성공 `reply_actions(action='sent', external_reply_id)` + `matched_posts.status='replied'` → **200** `{ action:'sent', external_reply_id }`. 실패 → `reply_actions(action='failed', error)` + status 복귀 `reviewing` → **502** `{ action:'failed', detail }`.
3. `can_write=false`(네이버/커뮤니티): 전송 안 함. `reply_actions(action='approved')` 기록 + status `replied` → **200** `{ action:'approved', clipboard_body }`(수동 복사용).
- 멱등성: `reply_actions` partial unique(action='sent')로 DB가 이중 sent 차단. 재요청은 409.
- 입력 검증(모두 CAS 클레임 전 — 상태 안 건드림): `final_body` 공백뿐/2000자 초과 422 ·
  `template_id` 미존재/비활성 422 · `sns_account_id` 는 본인 계정만(admin 전체)·active·
  소스 타입과 플랫폼 일치, 아니면 422.
- 전송 상한 120초 — 초과 시 강제 취소 후 실패 처리(502·재시도 가능).
- 정체 회수: `sending` 클레임 후 10분 경과 시 sweep 이 `reviewing` 으로 복귀시킨다(재시도 가능).
  상태 갱신은 클레임 시각 펜싱 — 회수 후 사람이 바꾼 상태를 늦은 요청이 덮어쓰지 않는다.

### `POST /api/matches/{id}/ignore` (CSRF)
→ `matched_posts.status='ignored'` → 200 `{ status: "ignored" }` + `reply_actions(action='canceled')` audit.
`new|reviewing` 에서만 가능 — 그 외 409, 없는 매칭 404.

### `POST /api/matches/{id}/retry` (CSRF)
전송 실패건 수동 재시도(FR-13). status가 `reviewing`이어야 함(그 외 409) → approve와 동일 CAS 경로 재실행. 새 `reply_actions` 행.
**Req 는 approve 와 동일**(`final_body` 필수 — 서버는 이전 시도 본문을 재사용하지 않는다). body 없이 호출하면 422.

---

## 감사 로그 (reviewer/admin)

### `GET /api/reply-actions?match_id=...`
200 `[{ id, matched_post_id, reviewer_user_id, action, external_reply_id, error, created_at }]` (append-only).

---

## 헬스

### `GET /api/health`
200 `{ status: "ok|degraded", db: "ok|error:...", poller: { last_tick, sources: [{ source_id, health_status, last_success_at }] } }`
> M0 현재: `{ status, db }`만. poller 필드는 M1에서 추가.

---

## 상태코드 규약

| 코드 | 의미 |
|---|---|
| 200/201/204 | 성공 |
| 400/422 | 잘못된 입력(422=검증 실패, 예: regex) |
| 401 | 미인증 |
| 403 | 권한 부족 / CSRF 실패 |
| 404 | 리소스 없음 |
| **409** | **approve 경쟁(CAS 0행) — 이미 처리 중/완료** |
| 502 | 외부 SNS 전송 실패 |
