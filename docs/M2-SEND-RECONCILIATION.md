# M2 선결 설계 — OQ-1 결론 & unknown-outcome 조회·조정 절차

> PRD §8 M2 선결조건(승인 2차 리뷰 H-1) 이행 문서. 작성 2026-07-21(정성운/백엔드), 적대적 리뷰 1회 반영.
> 결론: **Threads API 는 idempotency-key 를 지원하지 않는다** → FR-14 의 차선책(at-most-once)을
> 유지하되, 타임아웃/응답유실 시 **조정(reconciliation)으로 실제 게시 여부를 확인한 뒤에만**
> 수동 재시도를 허용하는 절차를 M2 write 어댑터와 함께 구현한다.

## 1. OQ-1 조사 결과 (2026-07-21 조사 · 2026-07-22 R3 실측 반영)

| 질문 | 결론 | 근거 |
|---|---|---|
| 게시 idempotency-key 지원? | **미지원** | 공식 문서(Posts·Create Replies)에 idempotency/중복 방지 파라미터 언급 없음. 서드파티 API 평가(APIs.io)도 idempotency 0/9 |
| 게시 플로우 | 2단계: `POST /{user_id}/threads`(컨테이너 생성, 답글이면 `reply_to_id` 포함) → `POST /{user_id}/threads_publish`(발행, `creation_id`) | 공식 Posts/Create Replies 문서. 발행 성공 시 Threads Media ID 반환 |
| 컨테이너 수명 | **24시간 후 만료**. 발행 전 평균 30초 대기 권장 | 공식 문서 + 커뮤니티 구현체 다수 |
| 생성 직후 즉시 publish 가능? | **거부될 수 있음** — 즉시 호출은 400 `code=24`(subcode 4279009, "미디어를 찾을 수 없음", `is_transient=false`)를 관찰. **3초 뒤 같은 creation_id 로 성공**(텍스트 기준 — 30초 권장치는 미디어 처리 대비 보수치로 해석). **code 24 를 발행 미수행의 보장으로 삼지는 않는다**(1회 관찰·특정 subcode 기준 — 공식 계약 아님, 독립 리뷰 반영): 어댑터는 짧은 재시도 후 소진 시 **결과 불명**으로 분류해 조정(§3.4)에 넘긴다 | R3 실측 2026-07-22 |
| 이미 발행된 컨테이너에 `threads_publish` 재호출 | **HTTP 200 + 같은 media id 반환(에러 아님 — 사실상 idempotent).** 새 글이 생기지 않고 답글 쿼터도 재소비되지 않음(재호출 후 `reply_quota_usage` 불변 교차 확인). 관찰은 발행 직후(수 초~수 분) 재호출 1회 기준 — 24h 컨테이너 수명 내에서만 의미 | R3 실측 2026-07-22 (§4-R3) |
| 게시된 답글 조회 | `GET /{media_id}/replies`·`GET /{media_id}/conversation` — 필드 `id, username, text, timestamp, is_reply, replied_to, root_post, permalink` 등, cursor 페이지네이션(`reverse` 기본 true). **`limit=100` 허용 실측 확인**(400 아님 — 2차 리뷰 사소-3 해소) | 공식 Reply Management 레퍼런스 + R3 실측 2026-07-22 |
| "내가 쓴 답글" 전용 목록 | 전용 엔드포인트 **없음** → 조정은 대상 글 기준(`/{media_id}/replies`)으로 수행 | 공식 레퍼런스에 부재 |
| rate limit | 게시 250건/24h(포스트 기준, 문서 명시). **답글은 별도 한도 1,000건/24h 실측 확인** — `GET /me/threads_publishing_limit?fields=reply_quota_usage,reply_config` 로 사용량 조회 가능(`reply_config: {quota_total: 1000, quota_duration: 86400}`) | 공식 Posts 문서 + R3 실측 2026-07-22 |

**따라서 FR-14 의 "idempotency-key 지원 시 사용" 분기는 성립하지 않는다.** 남는 문제는
"타임아웃=미전송 처리(at-most-once)" 가정이 틀리는 창 — publish 요청이 실제로는 처리됐는데
응답을 못 받았거나(타임아웃/유실) **실패 응답(5xx)으로 받은** 경우, 사람이 retry 를 누르면
**이중 게시**가 된다. 이 창을 조정 절차로 닫는다.

## 2. 문제 정의 — unknown outcome 이 생기는 지점

M1 approve 경로(`app/api/matches.py::_approve`)에서 `send_reply` 실패는 전부 동일하게
`failed 로그 + reviewing 복귀`(= 즉시 retry 가능)로 회계된다. 그러나 실패에는 두 종류가 있고,
구분 기준은 "응답을 받았는가"가 **아니라** "게시(발행)에 도달하지 않았음이 보장되는가"다:

- **확정 실패(definite)**: 게시가 일어나지 않았음이 구조적으로 확실한 실패. retry 안전.
  - **컨테이너 생성 단계의 모든 실패** — 발행은 별도 호출이므로 게시 위험 없음
    (컨테이너만 생겼을 수 있으나 무해 — 24h 후 만료).
  - **publish 단계에서 요청이 수행되지 않았음이 명확한 응답**: 401/403(인증 거부),
    429(rate limit), 400(유효성 거부).
- **결과 불명(unknown)**: 게시됐을 가능성을 배제할 수 없는 실패. **이때의 retry 는 이중 게시 위험.**
  - publish 요청의 타임아웃(`asyncio.wait_for` 취소)·커넥션 유실·응답 미수신.
    (타임아웃 취소는 우리 코루틴만 멈출 뿐 외부 사이드이펙트를 중단시키지 않는다 — 2차 리뷰 H-1)
  - **publish 단계의 5xx 응답** — 서버가 발행을 완료한 뒤 응답 생성/전달에서 실패했을 수 있다.
    "응답을 받았다"는 것이 "발행 안 됐다"를 보장하지 않는다. 408/409 등 결과가 애매한
    코드도 동일 취급(적대적 리뷰 반영 — 응답 수신 여부 기준 분류는 5xx 경로로 창이 열린다).

2단계 플로우 기준 위험 창은 **publish 단계 하나**다. publish 요청이 우리 손을 떠난 뒤의
모든 불확실성(무응답이든 5xx 응답이든)이 위 unknown 에 해당한다.

## 3. 설계 — 결과 불명 시 조회·조정 절차

### 3.1 상태/데이터 확장 (aerich forward-only)

- `PostStatus` 에 **`verify_pending`** 추가: "전송 결과 불명 — 조정 대기". 이 상태에서는
  approve/retry 의 CAS 클레임 대상이 아니므로(**클레임은 `new/reviewing` 에서만**) 구조적으로
  재전송이 차단된다. 단 **ignore 는 verify_pending 에서도 허용**한다 — 전송이 없는 안전한
  동작이고, 조정이 장기 실패할 때 사람이 대상 글을 직접 확인하고 종료할 수 있는 탈출구다
  (§3.4-5). FE 에는 읽기 전용 뱃지(계약 변경 — \[FE 공유\] 이슈로 공유).
- `ReplyAction` 에 **`unknown`** 추가: 결과 불명 발생 audit. 에러 요약 포함 — 기존
  `_safe_error` 급 필터링 동일 적용(불변식 ③).
- `matched_posts` 에 **`verify_meta` JSONB** (nullable) 추가:
  `{target_media_id, container_id?, claim_ts, attempts, reviewer_id, final_body, sns_account_id,
  platform_username?}`.
  (`sns_account_id` 는 조정 조회의 인증 계정 + `platform_username` 참조용 — 구현 시 추가.
  `platform_username` 은 **결과 불명 시점의 판정 키 스냅샷** — 자격증명 교체(토큰 교체 API)로
  계정 신원이 바뀌어도 조정 판정이 오염되지 않게 한다. 토큰 교체 API 3차 독립 리뷰 blocker.)
  기본 필드는 **CAS 클레임 성공 직후·전송 호출 전에 기록**한다(예외 핸들러가 아니라) —
  프로세스 크래시로 예외 핸들러가 못 돈 잔재(sweep 회수분)에도 조정 재료(대상·본문·승인자)가
  남아야 하기 때문이다. 예외적으로 `platform_username` 스냅샷만 **결과 불명 예외 시점**에
  기록된다(전송 중 어댑터가 갱신한 값이어야 해서 — §3.6 크래시∧교체 복합 잔여의 원인).
  토큰 등 비밀 없음(불변식 ③ — final_body 는 사람이 승인한 게시 예정 본문).
  sent/확정 실패/ignore 로 종결되면 null 로 청소한다.
- `sns_accounts` 에 **`platform_username`** 추가: 조정 판정 키(§3.4-2, live). 계정 등록
  시점에 `GET /me?fields=username` 으로 확보·저장하고 write 어댑터가 **매 전송 직전**
  갱신한다(2차 리뷰 중요-1) — 조정 조회 시점에 확보하는 방식은 토큰 만료 시 판정 자체가
  불가능해지므로 전송 전에 미리 저장돼 있어야 한다.

### 3.2 어댑터 계약 변경 (`app/sources/base.py`)

`SendError` 를 세분화한다:

```python
class SendError(Exception): ...            # 확정 실패 — 게시 안 됐음이 보장됨. retry 안전
class SendOutcomeUnknown(Exception): ...   # 결과 불명 — 조정 필요. container_id 등 컨텍스트 보유
```

Threads write 어댑터 분류 규칙(§2 의 기준을 코드로 — 단계별 분류이지 "응답 수신 여부" 분류가 아니다):
- 컨테이너 생성 단계의 모든 실패 → `SendError`(확정 실패). 게시 위험 없음.
- publish 단계의 401/403/429 → `SendError`(인증/스로틀 게이트 — 요청 미수행이 명확).
- publish 단계의 **400 은 Meta error code 로 세분**(어댑터 1차 적대 리뷰 중요-1): 유효성/OAuth
  계열 code(100·190·200·10 등)만 `SendError`, **transient 계열(code 1 "API Unknown"·2 "API
  Service")과 code 불명은 결과 불명** — Meta 는 일시 오류도 HTTP 400 으로 내려보내는 사례가 있다.
- publish 단계의 **그 외 전부(5xx·408·409·목록 밖 4xx·타임아웃·커넥션 오류·응답 미수신)** →
  `SendOutcomeUnknown(container_id=...)`.
- `_approve` 의 `asyncio.wait_for` TimeoutError → `SendOutcomeUnknown` 과 동일 취급.
  (현재 M1 은 TimeoutError 를 failed 로 회계 — M1 은 write 어댑터가 없어(mock/can_write=False)
  실위험이 없었다. M2 에서 이 분기를 바꾼다.)

### 3.3 `_approve`/sweep 분기 변경 (`app/api/matches.py`, `app/reply.py`)

```
except SendError            → (기존) failed 로그 + reviewing 복귀 → retry 허용 (+verify_meta 청소)
except Exception (그 외 전부) → unknown 로그 + verify_pending 전이(펜싱 유지) + verify_meta 갱신
                              응답: 502 {"action":"unknown","detail":"전송 결과 확인 중 —
                              자동 조정 후 재시도 가능해집니다"}
```

**기본값은 unknown 쪽(fail-safe)** — SendOutcomeUnknown/타임아웃뿐 아니라 **정체불명 예외**
(어댑터 응답 파싱 버그 등)도 publish 요청이 나간 뒤일 수 있으므로 결과 불명으로 회계한다.
"확정 안전"(SendError)이 증명 책임을 진다(1차 구현 적대 리뷰 F-1).

추가 사전 가드(1차 구현 적대 리뷰 F-2·F-4):
- approve/retry 는 클레임 전에 `reply_actions(action='sent')` 존재를 확인 — DB 에 전송 증거가
  있으면 상태와 무관하게 409 + replied 정합 회복(partial unique 의 사전 방어층).
- 전송 가능(will_send) 소스의 approve 는 **`sns_account_id` 필수**(없으면 422) — 계정 없는
  unknown 은 조정 판정 키가 없어 영구 limbo 가 되기 때문. \[FE 공유\] 계약에 포함.

sweep(`reply.py::sweep_stuck_sending`)은 회수 대상을 **분기**한다(현행 일괄 reviewing 복귀에서
코드 변경):
- **`verify_meta` 있는 행**(Threads 전송 착수분) → **`verify_pending`** — 죽은 클레임 역시 결과
  불명이기 때문(현행 reviewing 복귀는 같은 이중 게시 창을 갖고 있다. 이 변경으로 FR-14 의
  sweep 도 안전해진다).
- **`verify_meta` 없는 행**(전송 착수 전 크래시, can_write=False 소스 포함) → (기존) `reviewing`
  복귀 — 전송이 시작되지 않았으므로 안전하고, Threads 조회 기반 조정 잡이 처리할 수 없는 행이
  verify_pending 에 갇히는 영구 limbo 를 막는다.

### 3.4 조정 잡 (스케줄러, poller 와 동일 인프라)

주기(예: 60초)로 `verify_pending` 행을 처리한다. 판정 재료(`target_media_id`·`claim_ts`·
`final_body`·`reviewer_id`)는 전부 `verify_meta` 에서 가져오고, 전송 계정 username 은
**`verify_meta.platform_username` 스냅샷과 `sns_accounts.platform_username`(live) 둘 다
후보**로 검사한다(4차 검증 리뷰) — 스냅샷은 자격증명 교체(신원 교체) 오염을 막고(3차 blocker),
live 는 핸들 변경(rename) 후 `/replies` 가 현재 핸들을 반환하는 계약일 경우를 놓치지 않는다
(2차 리뷰 중요-1). 후보 확대의 오판 방향은 "더 찾음"(replied 종결)이라 이중 게시 안전 쪽(§3.6).

1. `GET /{target_media_id}/replies?fields=id,username,text,timestamp` (필요 시 `/conversation`,
   cursor 순회는 claim_ts 이전 timestamp 가 나오면 중단 — 어댑터 계약 `fetch_replies(source,
   target_media_id, account, since≈claim_ts-스큐)` 의 since 가 중단 기준).
2. **우리 답글 판정**: `username == platform_username` AND `timestamp >= claim_ts - 여유 60s`
   AND `normalize(text) == normalize(final_body)`.
   - 여유 60s = 서버↔플랫폼 클럭 스큐 + 발행 처리 지연을 합산한 보수치(claim_ts 는 우리 서버
     시계, timestamp 는 Threads 시계).
   - normalize: NFC 정규화 + 연속 공백 축약 + 양끝 trim. 플랫폼이 본문을 변형(절단·URL 단축
     등)할 수 있으므로 완전일치 실패 시 prefix(최소 40자) 일치를 보조 판정으로 사용.
     `final_body` 가 40자 미만이면 보조 판정 없이 **완전일치만** 사용(짧은 문구 오탐 방지).
3. **발견** → 기존 sent 경로와 동일 트랜잭션: `status=verify_pending` **조건부(CAS) 갱신**으로
   `replied` 전이 + `reply_actions(sent, external_reply_id)` 기록. 행의 `reviewer`·`final_body`
   는 `verify_meta` 의 `reviewer_id`·`final_body` 를 **승계**한다(조정 잡은 사람이 아니므로
   해당 전송을 승인한 사람을 기록 — `reviewer` NOT NULL 유지). partial unique(action='sent')
   위반(IntegrityError) 시 기존 `_approve` 와 동일한 정합 회복 처리 — 조정 잡과 사람이
   경합해도 구조적으로 안전.
4. **미발견** → `attempts += 1`. `attempts >= 5`(약 5분) 이면, 먼저 `reply_actions(sent)` 존재를
   확인해 **DB 에 전송 증거가 있으면 replied 정합 회복**(좀비 요청의 사후 audit 경합 —
   1차 구현 적대 리뷰 F-2). 증거가 없을 때만 **미게시 판정**:
   `reply_actions(failed, "조정 완료 — 미게시 판정")`(reviewer·final_body 승계 동일) +
   `reviewing` 복귀(retry 허용). FE 에는 "재시도 전 대상 글에서 직접 확인 권장" 문구를 함께
   내려준다(잔여 위험 §3.6).
   - 근거: publish 는 텍스트 답글 기준 수 초 내 반영되고 30초 처리 지연 권장치를 감안해도
     5분이면 충분. 경계값은 운영 설정으로 노출(`RECONCILE_MAX_ATTEMPTS`).
5. 조회 자체가 실패하면 attempts 를 늘리지 않고 다음 주기로 — 조정은 read-only 라 반복해도
   부작용이 없다. 단:
   - 429/`RateLimitedError` → source rate-limit 회계(지수 backoff) 공유, backoff 중엔 조회 skip
     (불변식 ④).
   - 그 외 조회 실패(**401/403 토큰 만료·회수** 포함) → 소스 health 회계로 배지 가시화(FR-18,
     에러 원문은 서버 로그에만 — 불변식 ③). 토큰 복구 시 자동 재개.
   - 조회 실패가 지속돼도 행이 갇히지 않도록 **verify_pending 에서 ignore 허용**(§3.1)이
     사람의 탈출구다 — 대상 글을 직접 확인하고 종료할 수 있다. approve/retry 는 계속 409.

### 3.5 왜 이 설계인가 (대안 비교)

- **컨테이너 ID 재사용 재시도**(publish 만 재호출): R3 실측(2026-07-22)으로 "이미 발행된
  컨테이너 재발행 = HTTP 200 + 같은 media id(쿼터 재소비 없음)"가 **확인됐다** — 같은
  creation_id 에 대한 publish 는 사실상 idempotent 다. 따라서 **조정 1차 수단으로 승격
  가능**: verify_meta 의 container_id 로 publish 를 재호출하면 이미 발행된 경우 같은
  media id 를 돌려받아(외부 조회·텍스트 판정 없이) sent 확정할 수 있고, 미발행이었다면
  사람이 승인한 그 전송이 그대로 완료된다(새 전송 아님 — FR-13 충돌 없음). 적용은 후속
  구현(§4-R8) — 적용 시 §3.6 잔여창이 닫힌다. 한계: 컨테이너 24h 만료 후에는 불가하므로
  텍스트 판정(§3.4)은 폴백으로 유지한다.
- **자동 재전송**: FR-13(자동 재시도 금지) 위반. 채택 불가. (조정 잡의 sent/failed 기록은
  이미 사람이 승인한 전송의 **사후 회계**이지 재전송이 아니다 — FR-13 과 충돌 없음.)
- **사람에게 "직접 확인 후 재시도" 안내만**: 절차가 사람 기억에 의존 — 구조적 방어(불변식 ②)
  원칙과 어긋난다. 조정 잡이 확인을 대신하고, 사람은 확정 이후에만 retry 할 수 있게 한다.

### 3.6 잔여 위험 (명시)

미게시 판정(§3.4-4)은 "5분간 조회에서 못 찾았다"는 **증거 부재**를 근거로 retry 를 다시 여는
지점이다. 다음 경우 오판(실제로는 게시됨)이 가능하고, 그때의 retry 는 이중 게시가 된다:

- Threads 가 본문을 변형(URL 단축·멘션 재표기 등)해 완전일치·prefix 판정이 모두 깨지는 경우
  (URL 이 앞 40자 안에 있으면 prefix 판정도 실패).
- `/replies` 의 read-after-write 일관성이 보장되지 않거나, 스팸 필터 보류·지연 노출로 답글이
  5분 창 이후에 나타나는 경우.
- **크래시 잔재 + 자격증명 교체 복합**: publish 도중 프로세스 크래시로 `platform_username`
  스냅샷이 못 남은 행(§3.1 — 스냅샷은 결과 불명 예외 시점에 기록)에 대해, 조정 전에 같은
  계정의 자격증명이 **다른 신원으로 교체**되면 live 폴백 판정 키가 오염된다. 정상 결과 불명
  경로는 스냅샷으로 방어되고(3차 독립 리뷰 blocker 반영), 이 복합 케이스(크래시∧교체∧재승인)만
  잔여 — 완전 봉합은 publish 호출 전 스냅샷 영속화(후속, R8 과 함께 검토).

완화:
- 미게시 판정 후 FE 에 "재시도 전 대상 글 직접 확인 권장" 표시 — 사람이 최종 방어선.
- R3 실측(2026-07-22)으로 컨테이너 재발행 idempotency **확인됨** — 후속 구현(§4-R8)로
  조정 1차 수단 승격 시 이 잔여창이 닫힌다(24h 만료 전 한정 — 만료 후 폴백은 텍스트 판정).
- 오탐 방향(같은 계정의 수동 답글을 우리 것으로 오인)은 결과가 "replied 과소 확정"이라 이중
  게시를 만들지는 않으나, R4 테스트에 "수동 답글 혼입" 케이스를 포함한다.

## 4. M2 구현 체크리스트 (write 어댑터 착수 조건)

- [x] R1. 마이그레이션: `PostStatus.verify_pending`·`ReplyAction.unknown`·`matched_posts.verify_meta`
      ·`sns_accounts.platform_username` — 마이그레이션 4. (username 확보 API 연동은 Threads
      write 어댑터 PR 에서 — `GET /me?fields=username`)
- [x] R2. `SendOutcomeUnknown` 계약(publish 5xx/408/409 포함 — §3.2 분류표) + `_approve`/sweep
      분기(`verify_meta` 유무로 verify_pending/reviewing 분기) + 게이트: verify_pending 에서
      approve/retry 409·**ignore 허용**
- [x] R3. **실측 완료(2026-07-22, 테스트 계정 실게시 1건)**: ① 발행된 컨테이너 재publish =
      200 + 같은 media id(idempotent, 쿼터 재소비 없음) ② 생성 직후 즉시 publish 는 400
      code=24(subcode 4279009, 준비 전) 가능 — 3초 뒤 성공 ③ `/replies?limit=100` 허용
      (사소-3 해소) ④ 답글 쿼터 별도 1,000건/24h. → §1·§3.5·§3.6 갱신 완료. 어댑터에
      publish code-24 짧은 재시도(같은 creation_id·소진 시 **결과 불명→조정 폴백**) 반영
      — 24 를 확정 실패로 승격하지 않음(N=1 근거 부족, 독립 리뷰 blocker 반영)
- [x] R4. 조정 잡(`app/reconcile.py`) + normalize 판정 단위 테스트(수동 답글 혼입·40자 미만
      본문·URL 포함 본문(앞/뒤 위치별)·timestamp 창·절단 prefix 판정 — `tests/test_reconcile.py`.
      동일 본문 답글 다수는 첫 매치 채택 — external_reply_id 오기록 가능성은 §3.6 과 같은
      "과소 확정" 방향이라 이중 게시 무관)
- [x] R5. FE 계약 공유: `verify_pending` 상태(ignore 만 허용)·`action:"unknown"` 응답·미게시 판정
      후 "직접 확인 권장" 문구·write 소스 approve 의 `sns_account_id` 필수(422) — \[FE 공유\]
      이슈 #62 발행(2026-07-23)
- [x] R6. PRD FR-14 문구를 본 설계로 갱신(§10 OQ-1 닫힘)
- [x] R7. **Threads write 어댑터 가드레일** — `app/sources/threads.py` 구현 반영:
      read 경로 403/429→`RateLimitedError` 매핑(불변식 ④), 토큰은 Authorization 헤더로만
      + 에러 요약은 Meta code/type 만(message 는 요청 echo 가능성으로 배제 — 불변식 ③),
      `platform_username` 은 **send_reply 의 전송 직전 단계에서 매번 갱신**(등록 시점 네트워크
      의존 제거 + 핸들 변경 시 stale 판정 키로 인한 결정적 오판 방지 — 이 단계 실패는 전송
      전이라 확정 실패로 안전), Protocol 명시 상속 없음(덕타이핑), publish 확정 실패는
      401/403/429 + 400 의 유효성/OAuth 계열 code 만(§3.2 세분 규칙) — 그 외 전부 결과 불명
      (fail-safe).
      잔여(후속): OAuth 콜백(API-SPEC §SNS 계정, OQ-2 심사 후).
      — 2차 리뷰 R-2(조정 전용 실패 health 깜빡임)·R-5(reviewer FK 방어)는 2026-07-22
      처리 완료: R-2 는 poller `_reconcile_failing` 틱 집계로 수집 성공의 ok 복귀를 억제,
      R-5 는 RESTRICT 실PG 회귀 테스트 + verify_meta.reviewer_id 잔여 경로 주석
      (`models.ReplyActionLog.reviewer`) 으로 고정
- [ ] R8. **조정 1차 수단 승격**(R3 실측 근거): 조정 잡이 verify_meta 의 `container_id` 로
      `threads_publish` 를 재호출 — 200 + media id 면 그 id 로 즉시 sent 확정(텍스트 판정
      불필요·§3.6 잔여창 폐쇄), 컨테이너 만료(24h)·재호출 불가 시에만 현행 텍스트 판정 폴백.
      §3.5 대안 비교의 확정 서술 참조. 착수 전 FR-13(자동 재시도 금지)과의 정합 —
      사람 재클릭 없는 시점의 외부 write 허용 여부 — 를 명시적으로 결정할 것(독립 리뷰 L2)
- [x] R9. **poll↔reconcile 상태 회계 잔여 Medium 2건**(R-2 검증 리뷰, 2026-07-22 —
      배지 정확도 문제로 발송 안전과 무관, 급하지 않음):
      ① poll 성공의 `_fail_counts` 무조건 리셋 — 조정 지속 실패(429 포함) 중에도 수집
      성공마다 카운터가 0 이 되어 지수 backoff 가 최소치(60초)에서 재시작(불변식 ④ 약화)
      + `_DOWN_AFTER_FAILURES` 미도달/down→degraded 역전 가능. 최소안: `_reconcile_failing`
      소스는 pop 생략, 정석: poll/reconcile 실패 카운터 분리.
      ② `_settle_sent` 예외 재raise 시 해당 틱 outcome 미집계 — 조회는 성공했는데
      `_reconcile_failing` 해제가 기존 플래그 유무에 따라 지연되는 이력 의존 동작.
      최소안: 호출부 try/except 로 로그 후 "fetch_ok" 반환(롤백·verify_pending 유지 그대로라
      전송 안전성 불변) + 회귀 테스트(기존 플래그 + non-sent IntegrityError → 해제 확인).
      — **처리(2026-07-23)**: ① 은 정석안 — `poller._reconcile_fail_counts` 분리(429 는
      최소안으로 커버 안 됨) + health 는 두 카운터 최대 기준(양방향 역전 방지), 해제는
      reconcile_tick 틱말 집계(플래그와 동일 기준)·forget_source. ② 는 최소안 그대로.
      회귀 테스트 3건(429 backoff 지수 유지·down 역전 방지·settle 실패 시 플래그 해제) —
      옛 코드에서 3건 모두 실패 확인.
      — **2차 독립 리뷰(codex, 2026-07-23) High 3건 반영**: poll 성공의 ok 복귀 조건에
      조정 실패 카운터 추가(429 는 플래그를 안 세워 플래그만으론 down→ok 깜빡임 재발),
      429 를 `"rate_limited"` outcome 으로 명시해 틱말 pop 에서 보호(같은 틱 mixed
      outcome 이 방금 증가한 카운터를 지우던 구멍 — 처리 순서도 `order_by(id)` 고정),
      미게시 판정 회계에도 ② 예외 흡수를 대칭 적용(reviewer FK RESTRICT 로 동일하게
      실패 가능). 회귀 테스트 3건 추가(총 6건) — 미수정 코드에서 3건 실패 재현.
      리뷰 Medium-1(기존 TOCTOU)은 R11 로 분리.

- [ ] R10. **Threads OAuth 잔여**(2차 적대 리뷰, 2026-07-22 — 병합 수용 판정, 후속):
      ① ~~수동 등록 계정(platform_user_id null)과 OAuth 재연동이 매칭되지 않아 계정 행이
      갈라질 수 있음~~ → **완료(2026-08-11, PR #86)**. 실측으로 현실화된 사고였다: 계정이
      26→31→32 로 증식하고, 옛 계정을 참조한 소스 110·119 가 고아가 되어 조용히 수집이
      멈췄다(`FetchError: config.sns_account_id 의 threads 계정을 찾을 수 없습니다`).
      처리 = **자격증명을 받는 모든 경로가 안정 식별자를 확보**한다 — 수동 등록(`POST`)과
      토큰 교체(`PUT`)가 `threads.fetch_profile()` 로 `/me` 를 조회해 `platform_user_id` 를
      채우고, 같은 신원이 이미 있으면 새 행 대신 자격증명 교체(계정 id 보존 = 소스 참조
      보존, `_adopt_same_identity` 를 OAuth 콜백과 공유). username 을 식별에 쓰지 않는
      High-5 결정은 유지. 프로필 조회 실패는 best-effort(등록을 막지 않음). 회귀 5건.
      ② `_raise_for_oauth_status` 가 3단계 공용이라 장기 전환·/me 의 401/403 이 "코드
      무효(400)"로 오분류될 여지(2차 L2, 실사용 가능성 낮음). ③ 테스트 공백: state
      위조 시 업스트림 0회·TTL 경계·2/3단계 RequestError·advisory lock 실동시성(2차 L3).
      ④ Dockerfile 이 --workers 1 을 암묵 보장 — in-memory state 의존 명시 검토(2차 L4).
      ⑤ **기존 고아 데이터 정리 미완**: 소스 110·119 는 여전히 사라진 계정 26·31 을
      참조한다(코드 수정은 재발 방지일 뿐 기존 행을 고치지 않는다). 비활성 상태라 폴링은
      멈춰 있고, 화면에는 `down`/`degraded` 배지로 남는다.

- [x] R11. **`_record_failure` 일반 실패의 무조건 `backoff_until=None` 저장**(R9 2차 리뷰
      Medium-1 — PR #63 이전부터 존재, 이번 범위 제외): poll↔reconcile 이 같은 루프에서
      interleave 될 때 stale `Source` 객체의 일반 실패 회계가 다른 채널이 방금 건 미래
      backoff 를 덮어쓸 수 있는 TOCTOU(불변식 ④). 성공 경로에 이미 있는 "미래 backoff
      존중" 가드(poll_source 의 재조회)를 실패 경로에도 적용하거나, 조건부 원자 update
      (`backoff_until__lte=now` 류)로 제한. 빈도 낮음(두 채널이 수초 내 교차 실패해야 발생).
      — **처리(2026-07-23)**: 조건부 원자 update 안 채택 — 일반 실패의 backoff 정리를
      `filter(id=…, backoff_until__lte=now).update(backoff_until=None)` 로 제한해 만료분만
      지운다(재조회 가드와 달리 재조회↔저장 사이 경쟁 창 자체가 없음). 회귀 테스트 2건
      (미래 backoff 보존 — 미수정 코드에서 실패 확인 · 만료 backoff 정리 유지).
      — **독립 리뷰(codex, 2026-07-23)**: diff 내 Critical/High 없음 — 봉합 확인(수정 전
      코드에서 회귀 재현까지 검증). Low 1건(assertion 강화) 반영, diff 밖 사전 존재
      Medium 2건은 R12·R13 으로 분리.

- [x] R12. **`_record_failure` rate-limit 분기가 기존의 더 긴 backoff 를 비교 없이 덮어씀**
      (R11 독립 리뷰 M1 — R11 이전부터 존재, 불변식 ④): reconcile 이 `Retry-After=3600`
      류 긴 backoff 를 건 직후 poll 429 회계가 자기 카운터 기준 짧은 backoff(1회차 60초)
      로 같은 소스를 덮어써 실제 만료 시각이 단축될 수 있음. reconcile.py 의
      `refresh_from_db()` 도 값을 max 비교하지 않아 보호 안 됨. 수정안: 후보와 현재 값 중
      `max()` 저장 또는 단일 조건부 UPDATE(`GREATEST` 류). 빈도 낮음(두 채널 교차 429).
      — **처리(2026-07-24)**: 조건부 원자 UPDATE 채택 — `filter(id).filter(Q(backoff_until
      __isnull=True) | Q(backoff_until__lt=candidate)).update(backoff_until=candidate)` 로
      후보가 현재값보다 클 때만 덮어써 GREATEST 를 단일 문으로 보장(재조회→비교 창 없음).
      회귀 테스트 2건(더 긴 기존 backoff 단축 방지 — 미수정 코드에서 실패 재현 · 정당한 연장 보존).
      — **독립 리뷰(codex, 2026-07-24) Medium 1건 반영**: health 단독 save 를 backoff 원자
      UPDATE **뒤**로 두면 두 UPDATE 사이 "health=degraded·backoff=NULL" 창에 poll_tick
      스냅샷이 `_is_due` 를 통과해 방금 429 맞은 소스에 추가 fetch 를 할 수 있음(④ 미시 위반).
      순서를 backoff-먼저로 반전(R13 성공 경로와 대칭) + 회귀 테스트 1건(health save 시점에
      backoff 선커밋 확인). Critical/High 0건, 불변식 ①②③ 범위 밖 확인.

- [x] R13. **poll 성공 경로의 DB 커넥션 레벨 잔여 TOCTOU**(R11 독립 리뷰 M2 — 기존 재조회
      가드 코드, 불변식 ④): asyncio 단일 루프라도 커넥션 풀 기본값(maxsize=5, `db.py` 가
      제한 안 함)으로 같은 프로세스에서 진짜 동시 트랜잭션이 가능 — reconcile 의 UPDATE 가
      커밋 전(행 잠금)일 때 poll 의 재조회 SELECT(READ COMMITTED)가 이전 값을 읽고, 이후
      `save()`(WHERE 가 PK 뿐, CAS 없음)가 최신 행을 stale 값으로 덮어쓸 수 있음. 수정안:
      성공 경로(108-120행)도 R11 과 같은 조건부 원자 update 로 교체, 또는 풀을
      `minsize=maxsize=1` 로 제한해 단일 워커 전제를 커넥션 레벨까지 정합화(처리량 영향).
      — **처리(2026-07-24)**: 조건부 원자 update 안 채택(풀 제한은 처리량 영향이라 배제) —
      성공 경로를 `save(["last_success_at"])` + 만료분만 지우는 원자 UPDATE(`WHERE backoff_until
      <=now`) + health=ok 원자 UPDATE(`WHERE backoff_until IS NULL`)로 분리해 재조회↔save 창을
      제거. PR #43 High-2·R9 2차 High-1·R-2 보호 그대로 유지(rowcount>0 일 때만 in-memory 정합).
      회귀 테스트 2건(커넥션 레벨 인터리브에도 미래 backoff 보존 — 미수정 코드에서 실패 재현 ·
      평시 만료 backoff 정리+health 회복). rowcount(int) 반환은 `matches.py` CAS 클레임과 동일 관례.

- [ ] R14. **poll↔reconcile 회계의 기존 구조적 잔여 3건**(R12/R13 독립 리뷰에서 발견 — 이번
      diff 가 새로 만든 것 아님, 불변식 ④/FR-18, 빈도 낮음·발송 안전 무관):
      ① `poll_tick` 이 틱 시작 스냅샷으로 `_is_due` 를 통과시킨 뒤 `adapter.fetch()` 직전
      재확인이 없어, 스냅샷 이후 다른 채널이 건 backoff 를 그 틱은 놓칠 수 있음.
      ② `health_status` 에 CAS/펜싱이 없어 동시 실패 시 `effective=max()` 계산과 저장 사이
      경쟁으로 down 이 degraded 로 순간 역전될 수 있음(R9① 이전부터 존재).
      ③ backoff 원자 UPDATE 성공 후 health save 가 부분 실패(크래시/취소)하면 backoff 만
      반영되고 health 는 stale — 관측(배지) 오차이며 backoff(④) 자체는 정합. 우선순위 낮음.

- [ ] R15. **계정 삭제가 참조 소스를 고아로 만든다**(2026-08-11 실측 — R10① 사고의 직접
      방아쇠): `DELETE /api/sns-accounts/{id}` 는 그 계정을 `config.sns_account_id` 로
      참조하는 소스가 있어도 그냥 지운다(FK 가 아니라 JSON 필드라 DB 백스톱이 없다).
      결과는 조용한 수집 중단 — 소스는 `enabled=true` 인데 매 틱 `FetchError` 만 남기고,
      `sources` 에 원인 컬럼이 없어(R17) DB 로는 진단이 안 된다. 수정안: 삭제 전 참조
      소스 검사 → 409(참조 목록 안내) 또는 소스 비활성화 후 삭제 허용. 소스 config 가
      JSON 이라 `config->>'sns_account_id'` 조회가 필요하다.

- [ ] R16. **`external_post_id` 기준 중복 발송 방지 부재**(2026-08-11 실측): dedup 키가
      `(source_id, external_post_id)` 라서 **같은 Threads 글이 다른 소스로 수집되면 새
      매칭**이 되고, 이중 발송 방어(CAS + `reply_actions` 부분 unique)는 전부
      `matched_post_id` 기준이라 막지 못한다. 즉 소스를 갈아타면 **이미 답글을 보낸 글에
      또 보낼 수 있다**. 실측: 글 `18059745272708845` 이 매칭 104(소스 110, `replied`)와
      5006(소스 121, `new`)에 동존 → 5006 을 `ignored` 로 수동 처리했다. 소스 삭제 시
      발송 이력까지 지우도록 완화한 뒤(2026-08-11 제품 결정)에는 "전에 답했다"는 단서가
      아예 남지 않으므로 사람이 알아챌 방법도 없다. 수정안: approve 전 같은
      `external_post_id` 의 `sent` 이력 검사(경고 또는 차단) — 불변식 ① 범위(사람 승인)
      안에서 리뷰어에게 보여주는 쪽이 자연스럽다.

- [ ] R17. **`sources` 에 실패 원인 컬럼이 없다**(2026-08-11 운영 실측): `health_status`
      가 `degraded`/`down` 이어도 **왜 실패했는지 DB 에는 없다** — 원인은 컨테이너 로그의
      `소스 수집/저장 실패 source=N` 스택뿐이라, 로그가 롤링되면 사라지고 대시보드에도
      띄울 수 없다. 실제로 소스 119 의 고아 참조를 찾는 데 이 때문에 시간이 걸렸다.
      추가로 `_fail_counts` 가 in-memory 라 **API 재기동 시 카운터가 0 으로 초기화**되어
      `down` 이던 소스가 `degraded` 로 보이는 것도 관측을 흐린다. 수정안: `last_error`
      (요약 문자열, 자격증명 echo 금지 — 불변식 ③) + `last_error_at` 추가, FE 배지
      hover 로 노출([FE 공유] 필요).

## 참고 문서

- Threads Posts(게시 2단계·250/24h·30초 대기): developers.facebook.com/docs/threads/posts
- Create Replies(`reply_to_id`): developers.facebook.com/docs/threads/retrieve-and-manage-replies/create-replies
- Reply Management 레퍼런스(`/replies`·`/conversation` 필드): developers.facebook.com/docs/threads/reference/reply-management
