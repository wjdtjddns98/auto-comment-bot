# Threads 앱 심사 제출 킷 (실행용)

> 2026-07-22 작성. 요건 근거는 `docs/THREADS-APP-REVIEW.md`. 이 문서는 콘솔에 실제로
> 넣을 값·문안·시연 시나리오만 담는다. 앱: sns-comment-boooot (ID 1031303652600125).

## 1. 사전 업로드 (nutti.co.kr — 콘텐츠/사이트 관리자)

| 파일(이 폴더) | 업로드 위치 | 콘솔 입력값 |
|---|---|---|
| `privacy-policy.html` | `https://nutti.co.kr/privacy.html` | 개인정보처리방침 URL |
| `data-deletion.html` | `https://nutti.co.kr/data-deletion.html` | 데이터 삭제 안내 URL |

업로드 전 `{{연락처 이메일}}`·`{{시행일}}` 채울 것. 삭제 안내 필드가 **URL 방식을 허용하지
않고 콜백 엔드포인트를 요구하면** 백엔드 콜백 구현+공개 배포가 선행돼야 한다(후속 PR —
`THREADS-APP-REVIEW.md` §3 두 번째 항목).

## 2. 신청 권한 (콘솔 App Review)

`threads_basic` / `threads_keyword_search` / `threads_content_publish` /
`threads_read_replies` (+ 콘솔이 요구하면 `threads_manage_replies` — §2 참조)

## 3. 사용 사례 설명문 (심사 제출용, 영문)

> 아래를 각 권한 요청의 "how will you use this permission" 에 맞춰 붙여 넣는다.

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

## 6. 제출 순서 체크리스트

- [ ] nutti.co.kr 에 privacy/data-deletion 페이지 업로드(§1) — 업로드 후 200 확인
- [ ] 콘솔 앱 설정: 개인정보처리방침 URL·삭제 안내 URL 입력, 필수 필드 잔여 확인
      (콜백 엔드포인트 강제 여부 여기서 판명 — §1 비고)
- [ ] 라이브테스트 C(실발송) 진행하면서 §4 시나리오 녹화
- [ ] App Review 에 권한 4~5종 신청 + 설명문(§3)·스크린캐스트·심사자 안내문(§5) 첨부
- [ ] (요구 시) 비즈니스 인증 진행
- [ ] 승인 후: 앱 Live 전환 → keyword_search 실수집 검증 → 폴링 예산 내 소스 확정
      (`THREADS-APP-REVIEW.md` §4 — 계정당 소스 7개 상한)
