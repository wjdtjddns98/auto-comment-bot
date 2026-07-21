# M2 선결 설계 — OQ-1 결론 & unknown-outcome 조회·조정 절차

> PRD §8 M2 선결조건(승인 2차 리뷰 H-1) 이행 문서. 작성 2026-07-21(정성운/백엔드), 적대적 리뷰 1회 반영.
> 결론: **Threads API 는 idempotency-key 를 지원하지 않는다** → FR-14 의 차선책(at-most-once)을
> 유지하되, 타임아웃/응답유실 시 **조정(reconciliation)으로 실제 게시 여부를 확인한 뒤에만**
> 수동 재시도를 허용하는 절차를 M2 write 어댑터와 함께 구현한다.

## 1. OQ-1 조사 결과 (2026-07-21 기준)

| 질문 | 결론 | 근거 |
|---|---|---|
| 게시 idempotency-key 지원? | **미지원** | 공식 문서(Posts·Create Replies)에 idempotency/중복 방지 파라미터 언급 없음. 서드파티 API 평가(APIs.io)도 idempotency 0/9 |
| 게시 플로우 | 2단계: `POST /{user_id}/threads`(컨테이너 생성, 답글이면 `reply_to_id` 포함) → `POST /{user_id}/threads_publish`(발행, `creation_id`) | 공식 Posts/Create Replies 문서. 발행 성공 시 Threads Media ID 반환 |
| 컨테이너 수명 | **24시간 후 만료**. 발행 전 평균 30초 대기 권장 | 공식 문서 + 커뮤니티 구현체 다수 |
| 이미 발행된 컨테이너에 `threads_publish` 재호출 | **문서화 안 됨** — 같은 media id 반환인지 에러인지 불명. M2 구현 시 실측 후 이 문서 갱신 | (실측 항목 §4-R3) |
| 게시된 답글 조회 | `GET /{media_id}/replies`·`GET /{media_id}/conversation` — 필드 `id, username, text, timestamp, is_reply, replied_to, root_post, permalink` 등, cursor 페이지네이션(`reverse` 기본 true) | 공식 Reply Management 레퍼런스 |
| "내가 쓴 답글" 전용 목록 | 전용 엔드포인트 **없음** → 조정은 대상 글 기준(`/{media_id}/replies`)으로 수행 | 공식 레퍼런스에 부재 |
| rate limit | 게시 250건/24h(포스트 기준, 문서 명시). 답글 별도 한도는 레퍼런스에서 재확인 필요 | 공식 Posts 문서 |

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
  `{target_media_id, container_id?, claim_ts, attempts, reviewer_id, final_body}`.
  **CAS 클레임 성공 직후·전송 호출 전에 기록**한다(예외 핸들러가 아니라) — 프로세스 크래시로
  예외 핸들러가 못 돈 잔재(sweep 회수분)에도 조정 재료(대상·본문·승인자)가 남아야 하기
  때문이다. 토큰 등 비밀 없음(불변식 ③ — final_body 는 사람이 승인한 게시 예정 본문).
  sent/확정 실패/ignore 로 종결되면 null 로 청소한다.
- `sns_accounts` 에 **`platform_username`** 추가: 조정 판정 키(§3.4-2). 계정 등록·토큰 갱신
  시점에 `GET /me?fields=username` 으로 확보·저장한다 — 조정 조회 시점에 확보하는 방식은
  토큰 만료 시 판정 자체가 불가능해지므로 전송 전에 미리 저장돼 있어야 한다.

### 3.2 어댑터 계약 변경 (`app/sources/base.py`)

`SendError` 를 세분화한다:

```python
class SendError(Exception): ...            # 확정 실패 — 게시 안 됐음이 보장됨. retry 안전
class SendOutcomeUnknown(Exception): ...   # 결과 불명 — 조정 필요. container_id 등 컨텍스트 보유
```

Threads write 어댑터 분류 규칙(§2 의 기준을 코드로 — 단계별 분류이지 "응답 수신 여부" 분류가 아니다):
- 컨테이너 생성 단계의 모든 실패 → `SendError`(확정 실패). 게시 위험 없음.
- publish 단계의 401/403/429/400 → `SendError`(요청 미수행이 명확).
- publish 단계의 **5xx·408·409·타임아웃·커넥션 오류·응답 미수신** → `SendOutcomeUnknown(container_id=...)`.
- `_approve` 의 `asyncio.wait_for` TimeoutError → `SendOutcomeUnknown` 과 동일 취급.
  (현재 M1 은 TimeoutError 를 failed 로 회계 — M1 은 write 어댑터가 없어(mock/can_write=False)
  실위험이 없었다. M2 에서 이 분기를 바꾼다.)

### 3.3 `_approve`/sweep 분기 변경 (`app/api/matches.py`, `app/reply.py`)

```
except SendError            → (기존) failed 로그 + reviewing 복귀 → retry 허용 (+verify_meta 청소)
except (SendOutcomeUnknown,
        TimeoutError)       → unknown 로그 + verify_pending 전이(펜싱 유지) + verify_meta 갱신
                              응답: 502 {"action":"unknown","detail":"전송 결과 확인 중 —
                              자동 조정 후 재시도 가능해집니다"}
```

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
`final_body`·`reviewer_id`)는 전부 `verify_meta` 에서, 전송 계정 username 은
`sns_accounts.platform_username` 에서 가져온다 — `_approve` 예외 경로든 sweep 회수분(크래시
잔재)이든 동일하게 동작한다(§3.1).

1. `GET /{target_media_id}/replies?fields=id,username,text,timestamp` (필요 시 `/conversation`,
   cursor 순회는 claim_ts 이전 timestamp 가 나오면 중단).
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
4. **미발견** → `attempts += 1`. `attempts >= 5`(약 5분) 이면 **미게시 판정**:
   `reply_actions(failed, "조정 완료 — 미게시 판정")`(reviewer·final_body 승계 동일) +
   `reviewing` 복귀(retry 허용). FE 에는 "재시도 전 대상 글에서 직접 확인 권장" 문구를 함께
   내려준다(잔여 위험 §3.6).
   - 근거: publish 는 텍스트 답글 기준 수 초 내 반영되고 30초 처리 지연 권장치를 감안해도
     5분이면 충분. 경계값은 운영 설정으로 노출(`RECONCILE_MAX_ATTEMPTS`).
5. 조회 자체가 실패하면 attempts 를 늘리지 않고 다음 주기로 — 조정은 read-only 라 반복해도
   부작용이 없다. 단:
   - 429/5xx → source rate-limit 회계(backoff) 공유(불변식 ④).
   - **401/403(토큰 만료·회수)** → audit 로그(에러 요약은 `_safe_error` 급 필터) + 소스 health
     배지로 가시화(FR-18). 토큰 복구 시 자동 재개.
   - 조회 실패가 지속돼도 행이 갇히지 않도록 **verify_pending 에서 ignore 허용**(§3.1)이
     사람의 탈출구다 — 대상 글을 직접 확인하고 종료할 수 있다. approve/retry 는 계속 409.

### 3.5 왜 이 설계인가 (대안 비교)

- **컨테이너 ID 재사용 재시도**(publish 만 재호출): 발행 성공 여부를 모르는 채 재호출하는
  것이므로, 재호출 의미론이 문서화되지 않은 현재로선 도박이다. R3 실측에서 "이미 발행된
  컨테이너 재발행 = 같은 media id 반환(에러 아님)"이 확인되면 **조정 1차 수단으로 승격**할 수
  있다(그 경우 사실상의 idempotency 확보 — 이 문서 갱신 후 적용, §3.6 잔여창도 닫힌다).
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

완화:
- 미게시 판정 후 FE 에 "재시도 전 대상 글 직접 확인 권장" 표시 — 사람이 최종 방어선.
- R3 실측으로 컨테이너 재발행 idempotency 가 확인되면 그것을 조정 1차 수단으로 승격(§3.5),
  텍스트 판정은 보조로 강등 — 이 잔여창이 닫힌다.
- 오탐 방향(같은 계정의 수동 답글을 우리 것으로 오인)은 결과가 "replied 과소 확정"이라 이중
  게시를 만들지는 않으나, R4 테스트에 "수동 답글 혼입" 케이스를 포함한다.

## 4. M2 구현 체크리스트 (write 어댑터 착수 조건)

- [ ] R1. 마이그레이션: `PostStatus.verify_pending`·`ReplyAction.unknown`·`matched_posts.verify_meta`
      ·`sns_accounts.platform_username`(등록·토큰 갱신 시 `GET /me?fields=username` 저장)
- [ ] R2. `SendOutcomeUnknown` 계약(publish 5xx/408/409 포함 — §3.2 분류표) + `_approve`/sweep
      분기(`verify_meta` 유무로 verify_pending/reviewing 분기) + 게이트: verify_pending 에서
      approve/retry 409·**ignore 허용**
- [ ] R3. **실측**: 발행된 컨테이너에 `threads_publish` 재호출 시 동작(및 답글 rate limit 한도)
      확인 → 본 문서 §1·§3.5 갱신(idempotent 확인 시 조정 1차 수단 승격)
- [ ] R4. 조정 잡 + normalize 판정 단위 테스트(동일 본문 답글 다수·**수동 답글 혼입**·40자 미만
      본문·URL 포함 본문·timestamp 창 판정)
- [ ] R5. FE 계약 공유: `verify_pending` 상태(ignore 만 허용)·`action:"unknown"` 응답·미게시 판정
      후 "직접 확인 권장" 문구 — \[FE 공유\] 이슈
- [ ] R6. PRD FR-14 문구를 본 설계로 갱신(§10 OQ-1 닫힘)

## 참고 문서

- Threads Posts(게시 2단계·250/24h·30초 대기): developers.facebook.com/docs/threads/posts
- Create Replies(`reply_to_id`): developers.facebook.com/docs/threads/retrieve-and-manage-replies/create-replies
- Reply Management 레퍼런스(`/replies`·`/conversation` 필드): developers.facebook.com/docs/threads/reference/reply-management
