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
200 `[{ id, name, type, config, poll_interval_sec, enabled, last_success_at, health_status, backoff_until }]`

### `POST /api/sources`
Req `{ name?: string(≤100), type: "threads|naver_cafe|community", config: {...}, poll_interval_sec }` → 201 source.

> `name`(선택): 표시용 이름(예: 커뮤니티/카페 이름). 미설정(null)이면 FE 는 config 값(URL·검색어)으로
> 폴백 표시. 공백뿐인 값은 null 로 정규화. PATCH 에서 명시적 `name: null` = 이름 제거.

> `config` 는 **타입별 스키마로 검증**된다(등록·수정 시점 422, 미지의 키 불허):
> - `community`: `{ "rss_url": "https://..." }` (http/https URL 필수)
> - `threads` (M2): `{ "query": "검색어(1~100자)", "sns_account_id": int }` — 키워드 검색
>   수집의 인증에 쓸 **본인 threads 계정** id. FE 소스 폼에 threads 타입 추가 필요([FE 공유]).
> - `naver_cafe`: 어댑터 미구현 — 등록 자체가 422
>
> 검증 실패 시 `422 { detail: "소스 설정이 올바르지 않습니다 — config.<필드>: <사유>[; ...]" }`.
> URL 은 표준형으로 정규화되어 저장·응답될 수 있다(예: `https://ex.am` → `https://ex.am/`).
> userinfo 포함 URL(`https://user:pw@...`)은 422 (자격증명이 config 로 저장되는 것 차단).

### `PATCH /api/sources/{id}`
Req(부분) `{ name?, enabled?, poll_interval_sec?, config? }` → 200. `config` 는 위 타입별 스키마로 검증(422). `name: null` = 이름 제거.
`enabled: true` 재활성화 시에는 저장된 config 도 재검증한다 — 무효 config 소스를 그대로 켤 수 없다(422).

### `DELETE /api/sources/{id}` → 204.
→ **수집물 cascade**(2026-07-22 제품 결정): 매칭 이력·비발송 감사 이력(approved 등)·
  스코프 키워드를 소스와 함께 정리한다. 전역 키워드는 무관.
→ 단 **실발송(sent)·결과 불명(unknown) 이력이 있거나 전송 진행 중(sending·verify_pending)
  매칭이 있으면 409**(감사·조정 재료 보호 — 불변식 ②. unknown 은 조정 미게시 판정이
  오판일 수 있어 흔적 보존). 이 경우 `enabled=false` 비활성화가 정식 경로.

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
→ **플랫폼별 형식 검증(등록 시점 422)**: `threads` 는 `credentials.access_token`(비어 있지 않은
  문자열) 필수 — 누락/오형식이면 `422 { detail: "credentials.access_token: ..." }`(입력값 echo 없음).
  FE 는 threads 선택 시 토큰 입력칸 하나만 노출하고 `{"access_token": <값>}` 으로 조립 권장(#32).

### `PUT /api/sns-accounts/{id}/credentials` (CSRF 필수) — 토큰 교체 (M2)
Req `{ credentials: {...} }` → 204 (본문 없음).
→ **삭제→재등록→소스 재연결 없이** 자격증명만 교체한다(토큰 만료/재발급 대응).
→ 등록과 동일한 플랫폼별 형식 검증: `threads` 는 `credentials.access_token`(비어 있지 않은
  문자열) 필수 — 누락/오형식 422(입력값 echo 없음). platform 은 저장된 계정 값 기준(전환 불가).
→ 교체 성공 시 `status` 가 `active` 로 복구되고 `token_expires_at` 은 null 로 초기화.
  (참고: 현재 만료/회수 상태의 **자동 회계는 미구현** — status 는 이 복구 외에는 향후 OAuth
  흐름에서만 변한다. 무효 토큰은 수집/전송 실패와 소스 health 배지로 드러난다.)
→ 본인 계정만(admin 은 전체) · 타인 것은 404 · 암호화 키 미설정 503(등록과 동일) ·
  삭제와의 동시 경합은 404 또는 409(재시도 안내) · body 에 credentials 외 키는 422.

### `DELETE /api/sns-accounts/{id}` → 204 (secrets cascade). 본인 것만(admin 은 전체) · 타인 것은 404.

---

## 매칭 (reviewer)

### `GET /api/matches`
Query: `status`(new|reviewing|sending|replied|ignored|verify_pending), `source_id`, `page`, `size`.
> `verify_pending`(M2): 전송 결과 불명 — 자동 조정 대기. **읽기 전용 뱃지로 표시**, approve/retry 는 409, ignore 만 가능. 조정이 끝나면 서버가 `replied`(게시 확인) 또는 `reviewing`(미게시 판정 — retry 재개) 으로 전이시킨다.
200 `{ items: [{ id, source_id, external_post_id, author, url, content, matched_keyword_id, published_at, matched_at, status }], total }`

### `GET /api/matches/{id}`
200 매칭 상세 + `reply_actions` 이력 요약.

### `POST /api/matches/{id}/approve` — 핵심 (CSRF 필수)
Req `{ template_id?: int, final_body: string, sns_account_id?: int }`
> **M2**: 전송 가능(write) 소스(threads)의 approve/retry 는 `sns_account_id` **필수** — 없으면 422.
> (계정 없이는 전송도, 결과 불명 시 조정도 불가.) 읽기 전용 소스(네이버/커뮤니티)는 종전대로 선택.
동작(MUST-FIX #1):
1. CAS: `status IN('new','reviewing') → 'sending'`. **0행이면 409 Conflict**(이미 처리 중/완료).
2. 소스 `can_write=true`: `adapter.send_reply` → 성공 `reply_actions(action='sent', external_reply_id)` + `matched_posts.status='replied'` → **200** `{ action:'sent', external_reply_id }`. 확정 실패 → `reply_actions(action='failed', error)` + status 복귀 `reviewing` → **502** `{ action:'failed', detail }`. **결과 불명**(타임아웃/응답유실/5xx, M2) → `reply_actions(action='unknown', error)` + status `verify_pending` → **502** `{ action:'unknown', detail:"전송 결과 확인 중 — 자동 조정 후 재시도 가능해집니다" }` — 이때 retry 버튼을 노출하지 말 것(409 남).
3. `can_write=false`(네이버/커뮤니티): 전송 안 함. `reply_actions(action='approved')` 기록 + status `replied` → **200** `{ action:'approved', clipboard_body }`(수동 복사용).
- 멱등성: `reply_actions` partial unique(action='sent')로 DB가 이중 sent 차단. 재요청은 409.
- 입력 검증(모두 CAS 클레임 전 — 상태 안 건드림): `final_body` 공백뿐/2000자 초과 422 ·
  `template_id` 미존재/비활성 422 · `sns_account_id` 는 본인 계정만(admin 전체)·active·
  소스 타입과 플랫폼 일치, 아니면 422.
- 전송 상한 120초 — 초과 시 강제 취소 후 **결과 불명 처리**(502 `action:'unknown'` → 조정 대기).
- 정체 회수: `sending` 클레임 후 10분 경과 시 sweep 이 회수한다 — 전송 착수분은 `verify_pending`
  (조정 대기), 착수 전 잔재는 `reviewing`(재시도 가능). 상태 갱신은 클레임 시각 펜싱 —
  회수 후 사람이 바꾼 상태를 늦은 요청이 덮어쓰지 않는다.
- 미게시 판정으로 `reviewing` 복귀한 매칭의 이력에는 `action='failed'` + "직접 확인 권장" 문구가
  남는다 — FE 는 retry 전 확인을 권장 표시.

### `POST /api/matches/{id}/ignore` (CSRF)
→ `matched_posts.status='ignored'` → 200 `{ status: "ignored" }` + `reply_actions(action='canceled')` audit.
`new|reviewing|verify_pending` 에서 가능 — 그 외 409, 없는 매칭 404.
(`verify_pending` 의 ignore 는 조정 장기 실패 시 사람의 탈출구 — M2 조정 설계 §3.1.)

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
