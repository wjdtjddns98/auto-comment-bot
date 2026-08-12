# Threads 앱 심사 제출 킷 (실행용)

> 2026-07-22 작성 · 2026-07-24 갱신 · **2026-08-11 갱신**. 요건 근거는 `docs/THREADS-APP-REVIEW.md`.
> 이 문서는 콘솔에 실제로 넣을 값·문안·시연 시나리오만 담는다. 앱: sns-comment-boooot (ID 1031303652600125).
>
> **2026-08-11 갱신 요지 (콘솔 직접 실측 + 실제 입력 반영)**
> ① **비즈니스 인증 통과** → 위저드 ① 인증 단계 ✅ (§6),
> ② 제출 위저드가 **실제 5단계**임이 확정 → §6 을 ⓐ~ⓔ 가상 목록에서 **실제 단계 상태표**로 재작성,
> ③ **§3 의 "붙여넣을 칸이 없다"는 오류였다** — 권한별 설명 칸이 실존하고, 실제로 입력한 문안 4건을 §3 에 수록,
> ④ ④ 데이터 처리·⑤ 검수자 지침 **입력 완료** → 실제 답안을 §5·§7-D 에 기록(반려 시 재사용),
> ⑤ 스크린캐스트에 **OAuth 승인 플로우 포함이 필수**임을 확인 → §4 시나리오에 0번 단계 추가.

## 1. 사전 업로드 (nutti.co.kr — 콘텐츠/사이트 관리자)

| 파일(이 폴더) | 업로드 위치 | 콘솔 입력값 |
|---|---|---|
| `privacy-policy.html` | `https://nutti.co.kr/privacy.html` | 개인정보처리방침 URL |
| `data-deletion.html` | `https://nutti.co.kr/data-deletion.html` | 데이터 삭제 안내 URL |

업로드 전 `{{연락처 이메일}}`·`{{시행일}}` 채울 것. 삭제 안내 필드가 **URL 방식을 허용하지
않고 콜백 엔드포인트를 요구하면** 백엔드 콜백 구현+공개 배포가 선행돼야 한다(후속 PR —
`THREADS-APP-REVIEW.md` §3 두 번째 항목).

## 2. 검수 대상 권한 (콘솔 App Review) — 2026-07-23 실측

콘솔이 **이용 사례(Use Cases) 기반**으로 바뀌어, 권한을 개별 신청하는 대신 "Threads API 액세스"
이용사례에 묶어 검수한다. **이미 검수 대상 추가가 끝난 6종**:

| 권한 | 상태 |
|---|---|
| `threads_basic` | ✅ 추가됨 |
| `threads_keyword_search` | ✅ 추가됨 |
| `threads_content_publish` | ✅ 추가됨 |
| `threads_read_replies` | ✅ 추가됨 |
| `threads_manage_replies` | ✅ 추가됨 (초판의 "콘솔이 요구하면" → 실제 포함으로 확정) |
| `public_profile` | ✅ 추가됨 (초판 미기재 — 콘솔이 함께 요구) |

`submission_id = 1032909602439530`. `threads_manage_mentions` 는 keyword_search 로 답글 자격이
충족되므로 신청하지 않는다(중복 경로).

## 3. 권한별 사용 설명문 (영문) — **콘솔에 실제 입력한 문안**

> **2026-08-11 정정**: 2026-07-23 에 "자유기술 칸이 없다"고 기록했으나 **오류였다.** 위저드
> ③ 허용되는 사용 방법에서 권한 카드의 `[시작하기]` 를 누르면 **모달**이 열리고, 그 안에
> 서술형 textarea 가 있다. 모달은 위저드 본문과 달리 **자동저장이 아니므로 `[저장]` 을 눌러야
> 반영**된다. 모달이 요구하는 3요소 = ① 앱에서 쓰는 방식 ② 사용자에게 주는 가치 ③ 필요한 이유.
> 아래 4건은 **2026-08-11 에 실제로 입력·저장 완료**했다. 반려·재입력 시 그대로 다시 쓴다.
> (`threads_manage_replies` 는 그 전에 이미 전체 완료 상태였고, `public_profile` 은 준수동의만 요구한다.)

공통 도입부(4건 모두 같은 문장으로 시작 — 내부 도구임을 먼저 못박는다):

> Nutti operates a pet-care brand. This app is an internal social-listening tool with
> human-approved replies, used only by our own team on our own Threads accounts.
> General consumers never log into this app.

### 3-1. `threads_content_publish`

> HOW WE USE threads_content_publish: the app publishes a reply to a matched public
> Threads post ONLY AFTER a human reviewer has read that post in our dashboard, written
> the reply text, and explicitly clicked Approve for that specific reply. The app has no
> auto-posting capability whatsoever - nothing is ever published without that human
> approval click on that exact reply.
> VALUE TO THE PEOPLE USING THE APP: our brand team can answer pet-nutrition questions
> helpfully and accurately from a single dashboard (for example, linking our free
> feeding-amount calculator at nutti.co.kr/calculator.html) instead of manually watching
> Threads all day, and every outgoing reply is reviewed by a person before it is sent.
> WHY THIS PERMISSION IS REQUIRED: publishing the approved reply from the Threads profile
> we own is not possible without threads_content_publish. Reply volume is human-paced
> (one manual approval click per reply), polling respects per-user query budgets, and we
> honor 429 responses and backoff.

### 3-2. `threads_read_replies`

> HOW WE USE threads_read_replies: after a human reviewer approves a reply and the app
> publishes it, the app reads the replies of that target thread to confirm that our own
> reply was actually posted. This is delivery reconciliation - it lets us close out a
> reply as confirmed, and it prevents duplicate replies when a network error leaves the
> publish result unknown. We only look for our own reply on the thread we just replied
> to; we do not collect, store, or analyze other users' replies.
> VALUE TO THE PEOPLE USING THE APP: the reviewer sees a trustworthy status for every
> reply they approved (confirmed as posted versus needs attention) instead of having to
> open Threads and check by hand, and the same check is what structurally stops the app
> from sending the same reply twice.
> WHY THIS PERMISSION IS REQUIRED: there is no other way to verify that a published reply
> actually exists on the target thread. Read calls happen once per approved reply (plus a
> bounded retry), respect per-user query budgets, and honor 429 responses and backoff.

### 3-3. `threads_keyword_search`

> HOW WE USE threads_keyword_search: our team configures a small set of pet-care keywords
> (for example, "how much should I feed my dog"). The app periodically searches public
> Threads posts matching those keywords and lists them in our internal dashboard so that
> a human reviewer can read each one and decide whether a helpful reply is worth writing.
> Matched posts are stored only to support that review queue and to avoid showing the same
> post twice; we do not build profiles of the authors and we do not use the content for
> advertising.
> VALUE TO THE PEOPLE USING THE APP: our reviewers find the questions where our answer is
> actually useful, in one queue, instead of manually scrolling Threads all day - and
> because the queue is what a human reads before approving anything, it is the first half
> of a review-then-approve workflow.
> WHY THIS PERMISSION IS REQUIRED: two reasons. First, discovering public posts that match
> our keywords is not possible without threads_keyword_search. Second, the Create Replies
> eligibility rules require this permission in order to reply to a post that our own
> account does not own. Polling runs on a default 300-second interval per source, respects
> per-user query budgets, and honors 429 responses and backoff.

### 3-4. `threads_basic`

> HOW WE USE threads_basic: when a member of our team connects one of our own brand
> Threads accounts to the dashboard, the app reads that account's basic profile (id and
> username) and its publishing quota. The username is shown in the dashboard so the
> reviewer can see which of our accounts a reply will be sent from, the id is the account
> identifier used on every subsequent API call, and the quota is checked so we stay inside
> the allowed publishing limits. We read this only for accounts our own team connects; we
> do not read profiles of other Threads users.
> VALUE TO THE PEOPLE USING THE APP: the reviewer can tell at a glance which brand account
> is connected and whether it is healthy before approving a reply, which prevents replying
> from the wrong account.
> WHY THIS PERMISSION IS REQUIRED: threads_basic is the base permission for all Threads
> API calls this app makes, and it is also required alongside threads_content_publish,
> threads_read_replies, and threads_keyword_search. Without it the app cannot identify the
> connected account at all. All calls respect per-user query budgets and honor 429
> responses and backoff.

## 4. 스크린캐스트 시나리오 (권장 4~5분, 한 테이크)

> ⚠️ **2026-08-11 확인 — OAuth 승인 플로우 포함이 필수다.** ③ 모달에 "스크린캐스트에 OAuth 승인
> 플로우도 포함해야 합니다" 가 명시돼 있다. 초판 시나리오는 대시보드 로그인부터 시작해 **이 단계가
> 빠져 있었다**. 아래 0번을 반드시 넣는다. 자막/내레이션 없이 화면만으로 충분.

0. **계정 연결 → threads.net 인가(동의) 화면에서 요청 권한 목록이 보이게 → 승인 → 콜백 →
   대시보드에 연결된 username 표시** *(OAuth 필수 요구 + `threads_basic`)*
1. 대시보드 → 소스(키워드) 목록 화면
2. 키워드 매칭된 공개 글이 목록에 뜬 것 확인 *(`keyword_search` — 심사 승인 전엔
   테스터 본인 글만 검색되므로, 테스터 계정(prayforyou_x) 글이 매칭된 화면으로 시연)*
3. 매칭 글 상세 → 답변 작성 → **Approve 클릭** (사람 승인이 유일한 게시 경로임이
   화면에 드러나게 — 승인 전엔 아무 일도 안 일어남을 잠깐 보여주기)
4. 전송 결과(sent/replied + 게시 링크) 확인 *(`content_publish`)*
5. Threads 실제 화면에서 답글 확인 + 대시보드 이력 화면 *(`read_replies` 는 조정 이력으로 시연)*

**녹화 전 사전 점검** (2026-08-11 실측 — 여기서 막히면 녹화가 중단된다):

- 호스트 포트 충돌: 8000 은 타 프로젝트(lead-crawler), 5433 은 별도 컨테이너(`nutti-pg`)가
  점유 중일 수 있다. 타 프로세스를 죽이지 말고 **`docker-compose.override.yml`**(untracked)로
  api→`8001:8000`, db→`5434:5432` 로 비켜간다. `ports` 는 override 시 append 되므로
  **`!override` 태그 필수**(없으면 원래 매핑이 남아 또 충돌). 컨테이너 내부 포트는 그대로라
  프론트 프록시(`VITE_PROXY_TARGET=http://api:8000`)·`DATABASE_URL` 은 수정 불필요.
- 브라우저는 **5173** 만 쓴다. 확인: `http://localhost:5173/health` 가 `{"status":"ok","db":"ok"}`.
- OAuth 3값(`THREADS_APP_ID`/`THREADS_APP_SECRET`/`THREADS_REDIRECT_URI`)이 컨테이너에 주입돼야
  authorize-url 이 503 을 내지 않는다. redirect_uri = `https://nutti.co.kr/threads-callback.html`.
- 개발 모드에서는 `keyword_search` 가 **테스터 본인 글만** 반환한다 → 2번 화면이 뜨려면
  테스터 계정에 매칭될 글이 실제로 있어야 한다.

## 5. 검수자 지침 (⑤ instructions-web-2) — **콘솔에 실제 입력한 문안**

> 2026-08-11 입력·저장 완료. Site URL 은 `https://nutti.co.kr/` 로 이미 설정돼 있다.

> HOW TO REACH THE APP: This app is an internal operations dashboard used only by Nutti's
> own team on our own Threads accounts. It is not a consumer product and has no public
> signup, so there is no login URL a reviewer can visit and create an account on.
> nutti.co.kr is our brand website (it hosts our privacy policy and data deletion pages),
> not the dashboard itself.
> WHAT THE APP DOES: our team configures a small set of pet-care keywords. The app searches
> public Threads posts matching those keywords and places them in an internal review queue.
> A human reviewer reads a matched post, writes a reply, and clicks Approve. Only that
> explicit approval click publishes the reply, and it is published from our own brand
> Threads account. The app has no auto-posting path at all - if nobody clicks Approve,
> nothing is ever sent. After publishing, the app reads the replies of that thread to
> confirm our own reply actually exists, which also prevents sending the same reply twice.
> HOW TO REVIEW IT: please refer to the screencast attached to the permissions in this
> submission. It shows the complete flow end-to-end in our running instance: keyword
> monitoring, human review of a matched post, the explicit Approve click, the reply being
> published on Threads, and the verification step that confirms the published reply.
> NOTE ON DEVELOPMENT MODE: while the app is in development mode, threads_keyword_search
> only returns posts owned by the authenticated tester, which is why the demo replies to a
> post owned by our own test account (prayforyou_x). The flow is identical for public posts
> once the permission is granted. If you need a live walkthrough or any additional
> recording, we will provide it on request.

**같은 화면의 다른 문항**: `fblogin-web-1`(Facebook Login 통합 여부) = **아니요**
— `backend/`·`frontend/` 전체에 `facebook`/`fbsdk` 매치 0건, Threads OAuth 만 사용한다(실측 근거).
`accesscode-web-1/2`·`geo-web-5`·`documents-web-1` 은 **전부 선택 사항**이며, 액세스 코드 칸은
불변식 ③(자격증명 노출 금지)에 걸리므로 **비워 둔다**.

## 6. 제출 위저드 실제 5단계 — 2026-08-11 실측 상태

> 초판의 ⓐ~ⓔ 목록은 실제 콘솔 구조와 달랐다. 실제 위저드는 아래 5단계이고, 스테퍼로 앞 단계로
> 점프할 수 없다(`[다음]` 으로만 진행). 위저드 본문은 자동저장이지만 ③ 의 권한별 모달은 `[저장]`
> 필요. 단계 저장 직후 "검토 필요" 배지가 떴다가 `[다음]` 으로 한 번 통과하면 ✅ 로 확정된다.
> "불완전 답변 시 권한 취소" 경고가 붙어 있으므로 **사실과 다른 답변 금지**(§7 의 확정 사실만 사용).

| # | 단계 | 상태 | 남은 일 / 담당 |
|---|---|---|---|
| ① | 인증 | **✅** | 비즈니스 인증 통과(2026-08-11). 해결책 = **`@nutti.co.kr` 도메인 이메일 확인**으로 전화번호 확인을 대체(상호 "누띠" 유지). 인증된 비즈니스 표기는 `Snscommentb0t`(ID 1954243385279897) |
| ② | 앱 설정 | **✅** | — |
| ③ | 허용되는 사용 방법 | **○** | **유일한 제출 블로커.** 설명문 4건은 입력 완료(§3). 남은 것 = **스크린캐스트 3건**(`read_replies`·`keyword_search`·`basic`) + **준수동의 체크 5건** → **운영자** |
| ④ | 데이터 처리 | **✅** | 2026-08-11 입력 완료(답안 §7-D) |
| ⑤ | 검수자 지침 | **✅** | 2026-08-11 입력 완료(문안 §5) |
| — | 최종 제출 | 대기 | ③ 완료 시 `[검수를 위해 제출]` 활성화. Meta 로그인·제출 클릭은 **운영자 전용** |

**완료된 선행 작업** (다시 하지 말 것):

- [x] nutti.co.kr 정책·삭제 안내 페이지 업로드 — 200 확인
- [x] 콘솔 앱 설정(개인정보처리방침·삭제 안내 URL)
- [x] OAuth 콜백 `nutti.co.kr/threads-callback.html` 200 배포 + 실연동 성공(계정 31)
- [x] 검수 대상 권한 6종 추가(§2)
- [x] 실발송·조정 라이브 검증(M2 수용 기준 충족)
- [x] `threads_manage_replies` 항목 전체 완료(콘솔)

**⚠️ 별도 트랙 — 액세스 인증(Tech Provider)**: 앱검수 제출을 막지는 않지만(①이 이미 ✅),
콘솔이 **"앱 1개 제한을 방지하려면 2026-10-10 까지 완료"** 라고 경고한다. URL
`developers.facebook.com/1954243385279897/access-verification/`, `[인증 시작]` 활성 상태.
신원·법적 선언이라 **운영자 전용**.

**스크린캐스트 업로드 크기 문제**: ③ 권한별 칸 업로드가 크기에서 막히면, **⑤ 의
`documents-web-1` 첨부는 파일당 2GB 한도**(.mp4/.mov/.zip 등, 다중 업로드 가능)이므로 원본을
그쪽에 올리고 지침에서 가리키는 우회로가 있다. 단 **파일 업로드는 운영자 직접**(OS 파일 선택창은
브라우저 자동화로 조작 불가).

## 6-A. 승인 후 첫날 체크리스트 (개발 모드에서 검증 불가능했던 것들)

> **왜 별도 절인가**: 승인 전까지 `keyword_search` 는 **인증 테스터 본인 글만** 반환한다
> (Meta 정책 — `THREADS-APP-REVIEW.md` §1). 그래서 파이프라인 전체(OAuth → 수집 → 매칭 →
> 승인 → 발송 → 조정 → 이력)는 본인 글로 이미 끝까지 실증됐지만, **아래 항목만은 승인 +
> Live 전환 뒤에야 처음 확인할 수 있다.** 20일 뒤 이 문서만 보고도 함정을 피할 수 있게
> 남긴다. **코드 수정은 필요 없다** — 어댑터는 이미 작성자 필터 없이 전체 검색을 요청하고,
> 본인 글만 오는 것은 Meta 쪽 제약이다(`app/sources/threads.py::fetch` 의 params 참조).

| # | 확인 | 함정 / 근거 |
|---|---|---|
| 1 | **앱 Live 전환** | 현재 "게시되지 않음". Live 여도 **미승인 권한은 작동하지 않으므로** 승인이 선행이다(§1 "앱 게시(Live) 후에만 가능"). |
| 2 | 타인 공개 글이 실제로 수집되는지 | 개발 모드에서 0건이던 것이 정상 동작. `q=간식` 실측이 전부 테스터 본인 글이었다(2026-08-11). |
| 3 | **첫 페이지 누락 여부** ⚠️ | `fetch` 는 **첫 페이지만** 수집한다("주기 폴링 + dedup 이 연속성을 보장" 전제). 본인 글만 나올 때는 6건이라 무해했지만, 타인 글이 쏟아지면 **폴링 주기(기본 300초) 사이에 첫 페이지를 넘길 수 있고 그 구간은 dedup 이 아니라 그냥 누락된다.** 한 주기당 유입량을 먼저 재고, 필요하면 ① cursor 순회(+상한 캡, `fetch_replies` 패턴 재사용) ② 주기 단축(쿼터·소스 상한과 상충) ③ 키워드 좁히기 중에서 고른다. |
| 4 | 타인 글 답글 실발송 | **리포스트(REPOST_FACADE)에는 답글 불가** — Meta 가 이를 `OAuthException code=10 (subcode 4279016)` 으로 내려줘 **권한 오류처럼 보인다.** 승인 직후 이 에러를 보면 "권한 못 받았나?" 로 오진하기 쉽다(`M2-SEND-RECONCILIATION.md` 참조). 어댑터 분류는 이미 400 유효성 계열로 처리돼 있다. |
| 5 | 쿼터 실측 → 소스 수 확정 | `keyword_search` **2,200쿼리/24h**(모든 앱 합산, 빈 결과 미산입). 기본 300초 = 소스당 288/일 → **계정당 7개 상한**. **승인 후엔 빈 결과가 줄어 실소비가 늘어난다** — 미산입 규칙 때문에 개발 모드 소비량으로 추정하면 과소평가된다. 답글은 별도 1,000/24h. |
| 6 | R16 중복 차단 실동작 | 여러 소스가 겹치는 키워드를 쓰면 **같은 글이 여러 소스에 잡히는 일이 흔해진다.** dedup 키가 `(source_id, external_post_id)` 라 각각 새 매칭이 되고, R16(approve 사전 검사)이 두 번째 발송을 409 로 막는다. 그 차단이 실제로 걸리는지 확인. |
| 7 | 제출 문안과 실제 동작의 정합 | §3·§5 에 **"one manual approval click per reply"·"no auto-posting capability"** 로 제출했다. 일괄 발송(FE)을 쓰면 이 문구와 어긋나므로, 운영 방식을 바꿀 경우 **문안도 함께 갱신**해야 한다("불완전·모호한 답변 시 플랫폼 액세스 권한 취소" 경고 대상). |

**참고 — 지금(개발 모드)도 타인 글을 볼 수 있는 경로**: `community` 타입 소스(RSS)는 Threads
제약과 무관하게 **타인 글을 수집한다**. 단 `can_write=false` 라 답글 전송은 안 되고 승인 시
`approved` + 수동 복사(clipboard)로만 처리된다. Threads 승인을 기다리는 동안 수집·매칭·리뷰
UI 를 실데이터로 돌려보려면 이 경로를 쓴다.

---

## 7. 선언 단계 답변 근거 — 우리 시스템의 확정 사실

> 아래는 문항을 추측한 "정답 문안"이 **아니라**, 어떤 문항이 나오든 **사실대로** 답하기 위한
> **검증된 근거 목록**이다. 코드·설계 문서로 확인되는 것만 적었다.

### A. 정책 준수(③ 준수동의) 근거

| 사실 | 근거 |
|---|---|
| **자동 게시 없음** — 모든 답글은 사람이 대시보드에서 그 문안을 명시적으로 Approve 클릭해야만 전송된다. 코드에 자동 발송 경로가 없다 | 불변식 ①(`CLAUDE.md` §2), `app/api/matches.py` 의 approve 가 유일한 전송 경로 |
| **이중 게시 구조적 차단** — CAS 클레임 + `reply_actions(matched_post_id) WHERE action='sent'` partial unique index 로 DB가 보장 | 불변식 ② |
| **내부 운영 도구** — 일반 소비자 가입/로그인 없음. 연동 계정은 자사 브랜드 계정뿐 | §3 공통 도입부 |
| **공식 공개 API만 사용** — headless 스크레이핑 없음 | 불변식 ④ |
| **Rate-limit 준수** — 소스별 폴링 주기(기본 300초), 429/403 지수 backoff, 서버 `Retry-After` 존중 | 불변식 ④, `app/poller.py` |
| **쿼터 상한을 설계에 반영** — keyword_search 2,200쿼리/24h 기준 계정당 소스 7개 상한 | `THREADS-APP-REVIEW.md` §4 |
| **답글 볼륨은 사람 속도** — 승인 클릭 1회 = 답글 1건 | 불변식 ① |

### B. 데이터 처리(④) 근거

| 사실 | 근거 |
|---|---|
| **토큰 분리 저장 + 암호화** — `sns_account_secrets` 별도 테이블에 Fernet 암호화 저장 | 불변식 ③ |
| **자격증명 노출 금지** — read path·API 응답·로그·트레이스백·URL 어디에도 노출하지 않으며, 응답 JSON 에 암호문/토큰 substring 이 없음을 테스트로 assert | 불변식 ③, 테스트 게이트 ②(`PRD.md` §8) |
| **수집 데이터 범위** — 공개 게시물의 본문·작성자·URL·게시시각 + 매칭 키워드를 Postgres 에 저장 | `ERD.md`, `app/poller.py` |
| **삭제 절차** — 소스 삭제 시 수집물·비발송 이력·스코프 키워드를 cascade 정리. 이용자용 삭제 안내 페이지 운영 | PR #49, `nutti.co.kr/data-deletion.html` |
| **접근 통제** — 로그인 필수 + admin/reviewer 역할 구분(권한 매트릭스 전면 검증은 v1 항목) | `PRD.md` §8, `app/api/` |
| **감사 기록** — 승인·전송·실패가 `reply_actions` 에 누가/언제/무엇을 기준으로 남는다 | 불변식 ②, `API-SPEC.md` |

### C. ⚠️ 선언 전 반드시 확인 — 현재는 **운영 배포 전**

v1(운영화) 마일스톤이 **미착수**다: Caddy TLS 배포·off-host 백업·위협모델 문서가 아직 없고 현재는
로컬 docker 스택에서만 돈다(`PRD.md` §8 의 v1 행). 따라서 **운영 환경 보안 조치를 이미 갖춘 것처럼
답하면 사실과 다르다.** 아래 §7-D 의 `requests-4` 답이 "해당 사항 없음" 인 이유가 바로 이것이다 —
문서화된 정책이 실제로 없으므로 있다고 선언하지 않았다.

### D. ④ 데이터 처리 — **콘솔에 실제 입력한 답안** (2026-08-11)

| 문항 | 입력값 | 근거·메모 |
|---|---|---|
| `processor-0` 플랫폼 데이터에 액세스하는 데이터 처리자/서비스 제공업체(본인 회사 포함) 있나 | **아니요** | 이 답을 고르면 `processor-2`(처리자 나열, 필수) 문항이 화면에서 사라진다 |
| `processor-2` 처리자 전부 나열 | (해당 없음) | 위 답으로 미노출 |
| `responsible-1` 데이터 관리자 법인명 | **누띠** | 사업자 상호. Meta 비즈니스 표기는 `Snscommentb0t` 이라 이름이 갈리는 점 인지 |
| `responsible-2` 국가 | **대한민국** | 드롭다운 클릭 시 검색창이 뜬다 — 타이핑 후 항목 클릭 |
| `requests-3` 지난 12개월 국가보안 요청에 개인데이터 제공 | **아니요** | |
| `requests-4` 공공기관 요청 관련 정책·절차 | **해당 사항 없음** | §7-C 대로, 없는 정책을 있다고 선언하지 않는다(허위 선언이 반려보다 위험) |
