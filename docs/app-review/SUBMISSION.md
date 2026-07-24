# Threads 앱 심사 제출 킷 (실행용)

> 2026-07-22 작성 · **2026-07-24 갱신**. 요건 근거는 `docs/THREADS-APP-REVIEW.md`. 이 문서는
> 콘솔에 실제로 넣을 값·문안·시연 시나리오만 담는다. 앱: sns-comment-boooot (ID 1031303652600125).
>
> **2026-07-24 갱신 요지 (2026-07-23 콘솔 실측 반영)** — 초판 이후 콘솔이 바뀌었다:
> ① 권한 개별 신청 → **이용 사례(Use Cases) 기반 검수**로 전환 → §3 용도 재정의,
> ② 실제 검수 대상 **권한 6종**(`public_profile` 추가) → §2,
> ③ 남은 단계가 **인증·선언 게이트 ⓐ~ⓔ**로 구체화 → §6 재작성 + 선언 대비 자료 §7 신설.

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
이용사례에 묶어 검수한다(각 권한의 표준 설명이 자동 연결됨). **이미 검수 대상 추가가 끝난 6종**:

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

## 3. 사용 사례 설명문 (영문) — 붙여넣을 칸이 없을 수 있음

> **2026-07-23 실측**: 이용사례 기반으로 바뀌면서 권한별 "how will you use this permission"
> 자유기술 칸이 보이지 않았다(각 권한 표준 설명이 자동 연결). 아래 문안은 **폐기하지 말고**
> 다음 용도로 쓴다 — ① 자유기술 칸(이용사례 설명·심사자 안내·선언 단계의 서술형 문항)이
> 나오면 그대로 사용, ② 스크린캐스트 구성과 심사자 안내문(§5)의 근거 문안, ③ 반려 시 보완 자료.

**App overview**

> Nutti operates a pet-care brand. This app is an **internal social-listening tool
> with human-approved replies** used only by our own team on our own Threads
> accounts. It monitors public Threads posts that match pet-nutrition keywords
> (e.g., "how much should I feed my dog"). A human reviewer reads each matched
> post in our dashboard and may write and explicitly approve a helpful reply
> (for example, linking our free feeding-amount calculator at
> nutti.co.kr/calculator.html). **The app has no auto-posting capability: nothing
> is ever published without a human clicking Approve on that specific reply.**
> General consumers never log into this app; the only connected accounts are the
> brand accounts our team operates.

**Per-permission usage**

- `threads_basic`: Retrieve the connected account's profile (id/username) and
  publishing quota; required base permission for all API calls.
- `threads_keyword_search`: Search public posts matching our configured
  pet-nutrition keywords so a human can review them. Also required by the
  Create Replies eligibility rules to reply to posts we do not own.
- `threads_content_publish`: Publish a reply **only after** a human reviewer
  explicitly approves that specific reply text in our dashboard.
- `threads_read_replies`: After publishing, read replies of the target post to
  confirm our reply was actually posted (delivery reconciliation) and prevent
  duplicates.

**Rate-limit compliance**: polling respects per-user query budgets
(default 300s interval), honors 429/backoff, and reply volume is human-paced
(each reply requires a manual approval click).

## 4. 스크린캐스트 시나리오 (권장 3~4분, 한 테이크)

라이브테스트 플로우와 동일 — 녹화만 겸하면 된다. 자막/내레이션 없이 화면만으로 충분.

1. 대시보드 로그인 → 소스(키워드) 목록 화면
2. 키워드 매칭된 공개 글이 목록에 뜬 것 확인 *(keyword_search — 심사 승인 전엔
   테스터 본인 글만 검색되므로, 테스터 계정 글이 매칭된 화면으로 시연)*
3. 매칭 글 상세 → 답변 작성 → **Approve 클릭** (사람 승인이 유일한 게시 경로임이
   화면에 드러나게 — 승인 전엔 아무 일도 안 일어남을 잠깐 보여주기)
4. 전송 결과(replied + 게시 링크) 확인 *(content_publish)*
5. Threads 실제 화면에서 답글 확인 + 대시보드 이력 화면 *(read_replies 는 조정
   이력으로 시연)*

## 5. 심사자 안내문 (Notes for reviewer, 영문)

> This is an internal operations tool (not a consumer app), so there is no public
> signup. The screencast shows the full flow end-to-end: keyword monitoring →
> human review → explicit approval → reply published on Threads. In development
> mode, keyword search only returns the authenticated tester's own posts, which
> is why the demo replies to a tester-owned post; the flow is identical for
> public posts once the permission is granted.

## 6. 남은 단계 체크리스트 — 2026-07-23 실측 기준

**완료된 선행 작업** (다시 하지 말 것):

- [x] nutti.co.kr 정책·삭제 안내 페이지 업로드 — 200 확인
- [x] 콘솔 앱 설정(개인정보처리방침·삭제 안내 URL) — 앱설정 항목 ✓
- [x] OAuth 콜백 `nutti.co.kr/threads-callback.html` 200 배포 + 실연동 성공(계정 31)
- [x] 검수 대상 권한 6종 추가(§2, `submission_id=1032909602439530`)
- [x] 실발송·조정 라이브 검증(M2 수용 기준 충족) + 심사용 녹화분 확보

**남은 단계** — 제출 화면의 `[~로 이동]` 버튼들. **"불완전 답변 시 권한 취소" 경고가 붙어 있으므로
사실과 다른 답변 금지**(§7 의 확정 사실만 사용):

| # | 단계 | 담당 | 비고 |
|---|---|---|---|
| ⓐ | **인증 — Tech Provider 신원/사업자** | **운영자 전용** | 개인정보·사업자 서류라 Claude 불가. **최종 게이트, 며칠~2주** — 가장 먼저 착수해야 전체 일정이 당겨진다 |
| ⓑ | 허용되는 사용 방법(정책 준수 선언) | 운영자 입력 | 답변 근거 §7-A |
| ⓒ | 데이터 처리(보안 선언) | 운영자 입력 | 답변 근거 §7-B. **현재 운영 배포 전이라는 점 주의** → §7-C |
| ⓓ | 스크린캐스트 첨부 | **운영자 전용** | 녹화분 180MB — **콘솔 업로드 한도(10MB) 초과**. YouTube **비공개 링크** 또는 운영자 직접 업로드 |
| ⓔ | 최종 제출 | **운영자 전용** | Meta 로그인·제출 버튼 |

**승인 후**: 앱 Live 전환 → `keyword_search` 로 **타인 공개 글** 실수집 검증 → 폴링 예산 내 소스 수
확정(`THREADS-APP-REVIEW.md` §4 — 계정당 소스 7개 상한).

---

## 7. 선언 단계(ⓑ·ⓒ) 답변 근거 — 우리 시스템의 확정 사실

> ⚠️ **콘솔의 실제 질문 문항은 미확인**이다(제출 화면에 진입해야 보임). 아래는 문항을 추측한
> "정답 문안"이 **아니라**, 어떤 문항이 나오든 **사실대로** 답하기 위한 **검증된 근거 목록**이다.
> 운영자가 실제 필드에 맞춰 골라 쓸 것. 코드·설계 문서로 확인되는 것만 적었다.

### A. 정책 준수(ⓑ "허용되는 사용 방법") 근거

| 사실 | 근거 |
|---|---|
| **자동 게시 없음** — 모든 답글은 사람이 대시보드에서 그 문안을 명시적으로 Approve 클릭해야만 전송된다. 코드에 자동 발송 경로가 없다 | 불변식 ①(`CLAUDE.md` §2), `app/api/matches.py` 의 approve 가 유일한 전송 경로 |
| **이중 게시 구조적 차단** — CAS 클레임 + `reply_actions(matched_post_id) WHERE action='sent'` partial unique index 로 DB가 보장 | 불변식 ② |
| **내부 운영 도구** — 일반 소비자 가입/로그인 없음. 연동 계정은 자사 브랜드 계정뿐 | §3 App overview |
| **공식 공개 API만 사용** — headless 스크레이핑 없음 | 불변식 ④ |
| **Rate-limit 준수** — 소스별 폴링 주기(기본 300초), 429/403 지수 backoff, 서버 `Retry-After` 존중 | 불변식 ④, `app/poller.py` |
| **쿼터 상한을 설계에 반영** — keyword_search 2,200쿼리/24h 기준 계정당 소스 7개 상한 | `THREADS-APP-REVIEW.md` §4 |
| **답글 볼륨은 사람 속도** — 승인 클릭 1회 = 답글 1건 | 불변식 ① |

### B. 데이터 처리(ⓒ 보안 선언) 근거

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
로컬 docker 스택에서만 돈다(`PRD.md` §8 의 v1 행). 따라서 ⓒ 에서 **운영 환경 보안 조치를 이미
갖춘 것처럼 답하면 사실과 다르다.** "불완전 답변 시 권한 취소" 경고가 붙은 단계이므로 운영자는
다음 중 하나로 처리할 것:

- 현재 상태를 그대로 기술한다(내부 도구·제한된 접근·배포 예정 시점 명시), 또는
- **선언 전에 v1 배포 항목(TLS·백업)을 먼저 끝내고** 사실이 된 뒤 답변한다.
