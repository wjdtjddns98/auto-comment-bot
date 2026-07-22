# Threads 앱 심사(App Review) 제출 요건 정리

> 작성 2026-07-22(정성운/백엔드) — 스크럼 ④ "keyword_search 심사 제출 요건 정리".
> 근거는 **[공식]**(developers.facebook.com 문서, 2026-07-22 열람) / **[실측]**(우리 앱
> sns-comment-boooot, 개발 모드) / **[서드파티]**(통합 가이드 — 콘솔에서 최종 확인 필요)로 구분.

## 1. 왜 심사가 필요한가 — 심사 없이는 서비스가 성립하지 않는다

- **[공식] 키워드 수집**: `GET /keyword_search` 는 `threads_basic` + `threads_keyword_search`
  필요. **미승인 앱은 인증 사용자 본인 게시물만 검색**된다(공개 글 검색 불가).
  [실측] 우리 앱(개발 모드, 미승인)에서는 500 `code=10` 으로 실패(OQ-2).
- **[공식·중대] 타인 글에 답글**: Create Replies 문서 기준, 답글 대상 루트 글의 **소유자가
  아니면** `threads_keyword_search` **또는** `threads_manage_mentions` 권한이 필요하다.
  즉 심사는 수집만이 아니라 **핵심 플로우(타인 매칭 글에 승인 답글) 전체의 전제조건**이다.
  [실측] 7/21~22 실발송 성공은 대상이 테스터 본인 글이었기 때문 — 미승인 상태의 성공 근거로
  일반화하면 안 된다. (리포스트 대상 code=10/4279016 함정은 `M2-SEND-RECONCILIATION.md` 참조.)
- **[공식] 일반 사용자 연동**: 앱 역할(테스터 등)이 없는 사용자의 권한 승인은 App Review 통과
  + 앱 게시(Live) 후에만 가능. 공개 프로필 사용자의 권한 부여는 90일 유효(장기 토큰 갱신으로 연장).

## 2. 심사 신청할 권한 (우리 사용 API 기준)

| 권한 | 우리 용도 | 근거 |
|---|---|---|
| `threads_basic` | 모든 호출 기본(`/me`, publishing_limit 포함) | [공식] Get Started |
| `threads_keyword_search` | 키워드 수집 + **타인 글 답글 자격** | [공식] Keyword Search·Create Replies |
| `threads_content_publish` | 답글 게시(컨테이너 생성·publish) | [공식] Get Started(게시 엔드포인트 필수) |
| `threads_read_replies` | 조정(reconcile) 조회 `GET /{media}/replies` | [공식] Get Started(댓글 GET 필수) |
| `threads_manage_replies` | (검토) 답글 POST 관리용 — Create Replies 요건에는 미기재. 콘솔 권한 요청 화면에서 실제 필요 여부 확인 | [공식] Get Started / 미확정 |

`threads_manage_mentions` 는 keyword_search 로 답글 자격이 충족되므로 신청 불요(중복 경로).

## 3. 제출 전 준비물 체크리스트

- [ ] **도메인 + 개인정보처리방침 URL** — 앱 설정 필수 필드. v1 도메인 구매와 함께 진행
      (정적 페이지면 충분: 수집 항목·이용 목적·보관 기간·삭제 절차·연락처).
- [ ] **[서드파티→콘솔 확인] Uninstall Callback URL / Delete Callback URL** — Threads 유스케이스
      설정의 필수 필드로 알려져 있음(HTTPS). **백엔드 구현 필요**: 연동 해제/데이터 삭제 요청
      수신 엔드포인트(서명 검증 포함) — 수신 시 해당 sns_account 비활성화 + secrets 삭제가 합리적.
- [ ] **스크린캐스트** — 권한별 사용 시연 영상(심사 제출물): 로그인 → 소스(키워드) 등록 →
      수집·매칭 확인(keyword_search) → 사람 승인 → 답글 게시(content_publish) → 이력 확인
      (read_replies). 현 로컬 스택(docker + Vite)으로 녹화 가능.
- [ ] **사용 사례 설명문** — "키워드 모니터링 + 사람 승인형 답변 도구(자동 게시 없음)" 를
      명확히: 모든 전송은 사람의 approve 클릭으로만 발생(불변식 ①) — 스팸/자동화 우려를
      선제 해소하는 핵심 논거.
- [ ] **심사자용 테스트 지침** — 테스트 계정·재현 절차(심사자가 직접 눌러볼 수 있게).
- [ ] **(콘솔 확인) 비즈니스 인증** — 출처마다 상이(불요/1~2주 소요 병기). 제출 화면에서
      요구되면 그때 진행.

## 4. 운영 설계에 미치는 제약 (심사 통과 후에도 유효)

- **[공식] keyword_search 쿼터: 사용자당 2,200쿼리/24h(롤링)** — 모든 앱 합산, **빈 결과
  쿼리는 미산입**. 같은 계정으로 여러 소스를 돌리면 합산되므로 폴링 예산이 상한이다:
  기본 주기 300초 = 소스당 288쿼리/일 → **한 계정당 소스 7개가 안전 상한**(8개=2,304>2,200).
  소스 수를 늘리려면 주기 상향(600초=144/일 → 15개) 또는 계정 분산. poller 는 429 백오프를
  이미 준수(불변식 ④)하므로 초과 시에도 자폭하진 않지만, 상시 초과 설계는 금지.
- **[공식] 게시 쿼터**: 포스트 250/24h, **답글 1,000/24h 별도**(`threads_publishing_limit` 로
  사용량 조회 가능 — R3 실측 확인, `M2-SEND-RECONCILIATION.md` §1).
- **[공식] 민감/불쾌 키워드는 빈 배열 반환** — 특정 키워드가 "수집 0건"이면 필터 가능성도 의심.

## 5. 진행 순서 제안

1. 도메인 구매 + 개인정보처리방침 정적 페이지 게시 (콘텐츠/마케팅과 협의)
2. 백엔드: uninstall/delete callback 엔드포인트 구현(+ 서명 검증) — 별도 PR
3. 콘솔에서 앱 설정 필드 채움(§3) → 권한별 심사 신청 + 스크린캐스트 첨부
4. 승인 → 앱 Live 전환 → 운영 계정 연동(OAuth 콜백 구현과 병행, API-SPEC §SNS 계정)
5. keyword_search 실수집 검증 → 폴링 예산(§4) 기준 소스 수 확정

## 참고 (2026-07-22 열람)

- Keyword Search: developers.facebook.com/docs/threads/keyword-search
- Create Replies(답글 자격 요건): developers.facebook.com/docs/threads/retrieve-and-manage-replies/create-replies
- Get Started(권한 목록·심사 안내): developers.facebook.com/docs/threads/get-started
- [서드파티] Mixpost/Postiz Threads 연동 가이드(콜백 URL 필수 필드·스크린캐스트) — 콘솔에서 재확인
