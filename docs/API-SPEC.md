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

## 소스 (읽기=로그인 사용자, 쓰기=admin)

> **권한(변경 2026-08-12, 이슈 #104)**: `GET` 은 **로그인 사용자 전체**(reviewer 포함),
> `POST`/`PATCH`/`DELETE` 는 **admin 전용**(403). 승인 화면이 소스 이름·health 배지·
> `type`(전송 가능 소스 판정)에 의존해서다 — reviewer 가 403 이면 소스 열이 `#8` 로 뜨고
> 전송 소스가 "복사 전용" 으로 오표기됐다. `config` 는 어댑터 스키마로 키가 고정돼 있어
> (`rss_url` / `query`·`sns_account_id`) 자격증명이 실릴 수 없다(불변식 ③).
> 키워드·템플릿도 같은 규칙이다.

### `GET /api/sources`
200 `[{ id, name, type, config, poll_interval_sec, enabled, last_success_at, health_status, backoff_until, last_error, last_error_at }]`

> **`last_error`·`last_error_at`(R17, 신규)**: 마지막 수집 실패 원인 요약과 시각. `health_status`
> 가 `degraded`/`down` 일 때 **왜 그런지**를 화면에서 보여주기 위한 필드다(이전에는 원인이
> 컨테이너 로그에만 있어 진단이 어려웠다). **수집이 성공하면 서버가 비운다**(null).
> 어댑터가 통제하는 안전 요약만 담기고, 예기치 못한 예외는 `"내부 오류: <타입명>"` 으로
> 축약된다 — 자격증명·토큰은 절대 담기지 않는다(불변식 ③).
> FE 는 health 배지 hover/툴팁으로 노출하면 좋다([FE 공유]).
>
> 참고: `health_status` 는 **연속 실패 1~4회 = `degraded`, 5회 이상 = `down`** 이고,
> 실패 카운터는 in-memory 라 **API 재기동 시 초기화**된다(재기동 후엔 `down` 이던 소스가
> `degraded` 로 보일 수 있다). 빈 결과 수집은 실패가 아니라 정상 성공(`ok`)이다.

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
→ **수집물 cascade**(2026-07-22 제품 결정, 2026-08-11 확대): 매칭 이력·감사 이력
  (approved·**sent·unknown 포함**)·스코프 키워드를 소스와 함께 정리한다. 전역 키워드는 무관.
→ 단 **전송 진행 중(sending·verify_pending) 매칭이 있으면 409** — 감사가 아니라 **정합성
  보호**다(`sending` 은 결과 기록 대상이 사라지면 유령 전송, `verify_pending` 은 조정 판정
  중이라 지우면 "게시됐는지 모름"이 영구 미해결). `enabled=false` 로 멈추고 조정이 끝난 뒤
  삭제할 수 있다.
→ **발송 이력 보존 목적의 409 는 제거됐다(2026-08-11 제품 결정)** — 이력 확인은 운영
  화면에서 하고 소스 정리는 원클릭이어야 한다는 판단. 불변식 ②(이중 발송 금지)는 영향
  없다(CAS 클레임 + `reply_actions` 부분 unique 인덱스가 그대로 보장). 다만 같은 글이 다른
  소스로 재수집되면 dedup 키가 `(source_id, external_post_id)` 라서 새 매칭이 되고, 과거
  발송 흔적이 없으면 **리뷰어가 "이 글에 전에 답했는지" 판단할 단서가 없다**는 점은 유의.

---

## 키워드 (읽기=로그인 사용자, 쓰기=admin — §소스 권한 주석 참조)

### `GET /api/keywords` → 200 `[{ id, pattern, match_type, enabled, source_scope }]`
### `POST /api/keywords`
Req `{ pattern, match_type: "substring|regex", source_scope?: int|null }` → 201.
422: regex 컴파일 실패 시.
### `PATCH /api/keywords/{id}` · `DELETE /api/keywords/{id}`

---

## 템플릿 (읽기=로그인 사용자, 쓰기=admin — §소스 권한 주석 참조)

### `GET /api/templates` → 200 `[{ id, name, body, enabled }]`
### `POST /api/templates` Req `{ name, body }` → 201.
### `PATCH /api/templates/{id}` · `DELETE /api/templates/{id}`

---

## SNS 계정 (로그인 사용자 — 본인 귀속 셀프서비스) — 토큰 배제

> 정책: 사용자는 **자기 SNS 계정만** 연동/조회/삭제한다. admin 은 전체 조회·삭제 가능(운영용).
> 연동 경로 2가지: ① 개발자 콘솔 발급 토큰 직접 등록(아래 POST) ② **Threads OAuth 동의
> 화면 연동**(threads-oauth — 앱 심사 요건이자 권장 경로).

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
→ **`threads` 는 등록 시 서버가 그 토큰으로 프로필(`/me`)을 조회해 안정 식별자를 확보한다.**
  **같은 Threads 신원이 이미 등록돼 있으면 새 계정을 만들지 않고 그 계정의 자격증명을 교체하고
  기존 계정을 201 로 반환한다**(계정 id 보존 — 소스 config 참조 유지). 즉 응답의 `id` 가 새 id 가
  아닐 수 있으므로 FE 는 반환된 `id` 를 그대로 쓸 것(신규 생성 가정 금지).
  프로필 조회가 실패해도(무효 토큰·업스트림 장애) **등록은 201 로 진행**된다 — 식별자만 비워두고
  무효 토큰은 이후 수집/전송의 health 회계로 드러난다. 동시 등록 경합은 409(재시도 안내).

### `PUT /api/sns-accounts/{id}/credentials` (CSRF 필수) — 토큰 교체 (M2)
Req `{ credentials: {...} }` → 204 (본문 없음).
→ **삭제→재등록→소스 재연결 없이** 자격증명만 교체한다(토큰 만료/재발급 대응).
→ 등록과 동일한 플랫폼별 형식 검증: `threads` 는 `credentials.access_token`(비어 있지 않은
  문자열) 필수 — 누락/오형식 422(입력값 echo 없음). platform 은 저장된 계정 값 기준(전환 불가).
→ 교체 성공 시 `status` 가 `active` 로 복구되고 `token_expires_at` 은 null 로 초기화.
  (참고: 현재 만료/회수 상태의 **자동 회계는 미구현** — status 는 이 복구 외에는 향후 OAuth
  흐름에서만 변한다. 무효 토큰은 수집/전송 실패와 소스 health 배지로 드러난다.)
→ **`threads` 는 교체 토큰으로도 프로필을 조회해 `platform_user_id`/`platform_username` 을
  채운다**(NULL 이던 기존 수동 등록 계정의 자기 치유 — 이후 OAuth 재연동이 같은 행으로 수렴).
  그 신원이 **이미 다른 계정에 연결돼 있으면 409** `"같은 Threads 계정이 이미 다른 항목에
  연결돼 있습니다 — 그 항목에서 교체해 주세요"`(변경 없음·롤백). 프로필 조회 실패는 교체를
  막지 않는다(식별자만 미갱신, 204).
→ 본인 계정만(admin 은 전체) · 타인 것은 404 · 암호화 키 미설정 503(등록과 동일) ·
  삭제와의 동시 경합은 404 또는 409(재시도 안내) · body 에 credentials 외 키는 422.

### `GET /api/sns-accounts/threads-oauth/authorize-url` — Threads 동의 화면 URL
200 `{ url }` — FE 는 "Threads로 연결" 버튼에서 이 URL 로 새 창/이동시킨다. URL 에는 서버
발급 **state**(요청 사용자 바인딩·10분 TTL·1회용)가 포함된다. 동의 완료 시
`https://nutti.co.kr/threads-callback.html?code=...&state=...` 로 리다이렉트되고, 그 페이지가
`code=...&state=...` 형태의 연동 값을 표시한다(수동 운반 — FE 는 이 문자열 또는 전체 URL
붙여넣기를 파싱해 code/state 로 분리 권장). 서버 OAuth 설정 미비 시 503.

### `POST /api/sns-accounts/threads-oauth` (CSRF 필수) — 코드로 계정 연동
Req `{ code, state, display_name? }` → 201 `{ id, user_id, platform, display_name, status, token_expires_at }`.
→ `state` 는 authorize-url 발급분과 일치해야 한다(발급 사용자·TTL·1회용 검증) — 불일치/만료
  400 "연동 세션이 만료되었거나 유효하지 않습니다"(재발급은 authorize-url 재호출).
→ 서버가 코드→단기→**장기(60일) 토큰** 교환 후 암호화 저장. `token_expires_at` 이 실제
  만료 시각으로 채워진다(수동 등록과의 차이). `platform_username`(표시·판정용)과 안정
  식별자(platform_user_id, 내부 전용 — 응답 미노출)도 연동 시점에 확보.
→ **같은 Threads 신원(안정 id) 재연동이면 새 계정을 만들지 않고 자격증명 교체**(계정 id
  보존 — 소스 config 참조 유지) + `status=active` 복구 + username 갱신(rename 반영).
  `display_name` 은 주면 갱신.
→ 코드 무효/만료/재사용 400(고정 메시지, echo 없음) · **Threads API 장애/네트워크 실패
  502**(재시도 안내) · OAuth 설정 미비 503 · 암호화 키 미설정 503 · 그 외 키 422(extra 금지).

### `DELETE /api/sns-accounts/{id}` → 204 (secrets cascade). 본인 것만(admin 은 전체) · 타인 것은 404.
→ **그 계정을 `config.sns_account_id` 로 참조하는 소스가 있으면 409**(R15)
  `"이 계정을 사용하는 소스가 있어 삭제할 수 없습니다(소스 3, 7) — 소스를 먼저 삭제하거나
  다른 계정으로 변경해 주세요"` — 참조 소스 id 를 문구에 담아 FE 가 안내에 쓸 수 있다.
  **비활성 소스도 참조로 센다**(다시 켜면 같은 고아 상태가 되므로). 소스 config 는 JSON
  필드라 FK 백스톱이 없어, 이 검사가 없으면 계정 삭제가 소스를 조용히 고아로 만든다
  (실측: 소스가 `enabled=true` 인데 매 틱 `FetchError` 만 남기고 수집이 멈춘다).

---

## 매칭 (reviewer)

### `GET /api/matches`
Query: `status`(new|reviewing|sending|replied|ignored|verify_pending), `source_id`, `page`, `size`.
> `verify_pending`(M2): 전송 결과 불명 — 자동 조정 대기. **읽기 전용 뱃지로 표시**, approve/retry 는 409, ignore 만 가능. 조정이 끝나면 서버가 `replied`(게시 확인) 또는 `reviewing`(미게시 판정 — retry 재개) 으로 전이시킨다.
200 `{ items: [{ id, source_id, external_post_id, author, url, content, matched_keyword_id, published_at, matched_at, status, sending_claimed_at }], total }`

> **`sending_claimed_at`(신규, 이슈 #101 요청 2-a)**: `new|reviewing → sending` 전이 시각
> (approve/retry 의 CAS 클레임이 찍는다). 처리 이력에 "전송중" 전이 시각을 표시하기 위한
> 필드. 클레임이 풀린 뒤(`replied`/`reviewing`)에도 마지막 시도 값이 남으므로, 화면에서는
> `status` 와 `reply_actions` 의 후속 행을 함께 보고 판단할 것. 아직 승인된 적 없으면 null.

### `GET /api/matches/{id}`
200 매칭 상세 + `reply_actions` 이력 요약.

### `POST /api/matches/render-template` — 템플릿 일괄 발송용 문구 미리보기
Req `{ template_id: int, match_ids: int[] (1~200) }` → 200 `{ items: [{ match_id, body, error }] }`.

> **전송하지 않는다.** 사람이 이 미리보기를 보고 approve 를 눌러야 나간다(불변식 ①).
> 상태를 바꾸지 않는 조회성 POST 라 **CSRF 불필요**(body 로 id 목록을 받아야 해서 GET 이 아니다).
> reviewer 도 호출 가능(일괄 발송 주체).

- **왜 필요한가**: 일괄 발송이 N건에 **완전히 동일한 문구**를 보내면 rate-limit 과 무관하게
  중복 콘텐츠로 스팸 판정될 수 있다. 서버가 건마다 변형을 독립적으로 뽑아 문구를 갈라준다.
- **치환 문법**(`app/templating.py`):
  - `{{a|b|c}}` — **랜덤 변형**. 후보 중 하나. 후보 앞뒤 공백은 서식이라 strip 되고, 빈 후보
    (`{{ !|}}`)는 "있거나 없거나" 를 뜻한다.
  - `{{author}}`·`{{keyword}}`·`{{url}}` — 그 매칭의 작성자·매칭 키워드·원문 링크.
    **원문 본문(`content`)은 제공하지 않는다** — 상대 글 복사는 스팸 신호다.
- **에러는 건별**: 미지 변수(오타)·값 없는 변수(author 미확보)·없는 매칭은 그 항목만
  `body=null` + `error` 문구. 한 건의 실패가 나머지 미리보기를 막지 않는다. FE 는 그 건을
  대상에서 빼면 된다. 오타를 조용히 비우고 게시하지 않는 것이 원칙.
- `items` 는 **요청 순서를 유지**한다. 없는/비활성 `template_id` 는 422, 빈 `match_ids` 422,
  미지 키 422(extra 금지).
- FE 는 받은 `body` 를 그대로 approve 의 `final_body` 로 보내면 되고, 사람이 수정해도 된다
  (그게 사람 승인이다). 길이 상한(Threads 500자)은 approve 가 검사한다.

### `POST /api/matches/{id}/approve` — 핵심 (CSRF 필수)
Req `{ template_id?: int, final_body: string, sns_account_id?: int }`
> **M2**: 전송 가능(write) 소스(threads)의 approve/retry 는 `sns_account_id` **필수** — 없으면 422.
> (계정 없이는 전송도, 결과 불명 시 조정도 불가.) 읽기 전용 소스(네이버/커뮤니티)는 종전대로 선택.
동작(MUST-FIX #1):
1. CAS: `status IN('new','reviewing') → 'sending'`. **0행이면 409 Conflict**(이미 처리 중/완료).
2. 소스 `can_write=true`: `adapter.send_reply` → 성공 `reply_actions(action='sent', external_reply_id)` + `matched_posts.status='replied'` → **200** `{ action:'sent', external_reply_id }`. 확정 실패 → `reply_actions(action='failed', error)` + status 복귀 `reviewing` → **502** `{ action:'failed', detail }`. **결과 불명**(타임아웃/응답유실/5xx, M2) → `reply_actions(action='unknown', error)` + status `verify_pending` → **502** `{ action:'unknown', detail:"전송 결과 확인 중 — 자동 조정 후 재시도 가능해집니다" }` — 이때 retry 버튼을 노출하지 말 것(409 남).
3. `can_write=false`(네이버/커뮤니티): 전송 안 함. `reply_actions(action='approved')` 기록 + status `replied` → **200** `{ action:'approved', clipboard_body }`(수동 복사용).
- 멱등성: `reply_actions` partial unique(action='sent')로 DB가 이중 sent 차단. 재요청은 409.
- **같은 게시물 중복 답글 차단(R16)**: 전송 소스의 approve/retry 는 클레임 전에 **같은
  `external_post_id` 를 가진 다른 매칭**에 `sent`·`unknown` 이력이 있는지 검사하고, 있으면
  **409** `"이 게시물에는 이미 답글을 보냈습니다(다른 소스로 중복 수집된 글) — 중복 답글은
  스팸으로 판정될 수 있어 차단합니다"`. 위 partial unique 는 `matched_post_id` 단위라
  **같은 글이 다른 소스로 재수집되면(dedup 키가 `(source_id, external_post_id)`) 막지
  못했다** — 소스를 갈아타는 것만으로 이미 답한 글에 두 번째 답글이 나가는 경로였다.
  `unknown`(결과 불명)도 차단 사유에 포함한다(게시됐을 가능성 배제 불가 → fail-safe).
  FE 는 이 409 를 "이미 답한 글" 안내로 노출하면 좋다([FE 공유]).
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

### `GET /api/reply-actions`
Query: `match_id`(선택 — 그 매칭 이력만), `limit`(선택, 1~500, **기본 200**).
200 `[{ id, matched_post_id, reviewer_user_id, action, template_id, external_reply_id, error, created_at }]` (append-only).

- **정렬 `created_at desc`**(동시각은 `id desc`). append-only 라 전량 반환하지 않고 **최신
  `limit` 건**만 준다 — 기본 200건을 넘겨 봐야 하면 `limit` 을 올린다(상한 500).
- 권한은 **로그인 사용자**(reviewer 포함) — 이 화면은 "누가 무엇을 승인했나"를 함께 보는 용도다.
- `final_body`·`sns_account_id` 는 **미포함**: 전자는 목록을 수십 KB 로 부풀리고, 후자는
  계정 셀프서비스 경계(본인 것만 조회)를 감사 로그가 우회하는 통로가 된다.
- `action` = `approved|sent|failed|canceled|unknown`. `matched_post_id` 는 매칭 상세로 링크하기
  위한 값이다(매칭 상세에 embed 되는 `reply_actions` 에는 없다 — 부모가 곧 그 값이라서).

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
