# frontend — 프론트엔드 영역 규칙

> 루트 `CLAUDE.md` 를 상속한다. 이 파일은 `frontend/**` 안에서만 적용되는 세부 규칙이며,
> 루트 4절("프론트엔드")의 확장이다. 충돌하면 루트가 우선한다.

## 1. 경계

- 이 디렉터리는 **프론트엔드 담당 소유**다(`.github/CODEOWNERS`: `frontend/**` → 프론트).
- `backend/` 이하를 **직접 수정하지 않는다**. 백엔드 변경이 필요하면 이슈 또는 PR 코멘트로 요청한다
  (요청 이슈는 백엔드 담당 @멘션 + assignee 지정 — 안 하면 알림이 안 간다).
- 반대로 백엔드는 `frontend/**` 를 수정하지 않는다. CI(`.github/workflows/ci.yml`) 는 백엔드 소유다.

## 2. 스택

- React 18 + TypeScript 5.7, 번들러 Vite 6, 스타일 Tailwind CSS 4(`@tailwindcss/vite`).
- 라우팅 `react-router-dom` 6, 서버상태 `@tanstack/react-query` 5. **전역 스토어는 두지 않는다**
  (서버상태는 Query, 화면 로컬 상태는 `useState` 로 충분 — YAGNI).
- 테스트 러너 vitest 3. 별도 러너를 추가하지 않는다.

## 3. 게이트 (커밋 전 필수)

```bash
cd frontend
npm run build   # tsc --noEmit && vite build
npm test        # vitest run (watch 아님, 1회 실행 후 종료)
```

**둘 다 green 이어야 커밋한다.** CI 의 `프론트엔드 빌드 & 테스트` 잡이 정확히 이 두 명령을 그대로
돌린다(PR #114). 빌드만 통과시키고 올리면 CI 에서 잡힌다.

## 4. CI 경로 필터

- CI 는 단일 워크플로 + 영역 path 필터 + 단일 게이트(`ci-ok`) 구조다.
- 이 디렉터리만 바뀐 PR 은 **백엔드 잡(Lint & Test, PostgreSQL 마이그레이션)이 skip** 된다.
  루트 `CLAUDE.md` 나 `docs/` 를 같이 건드리면 `backend=true` 가 되어 백엔드 잡도 함께 돈다.
- 판정 근거는 `변경 영역 감지` 잡 로그의 `backend=... frontend=...` 줄에서 바로 확인한다.

## 5. 로컬 실행

```bash
npm run dev        # :5173, /api·/health 를 http://localhost:8000 으로 프록시
npm run dev:mock   # 백엔드 없이 모의 서버(src/lib/mock/)로 구동
```

- 프록시 대상은 `VITE_PROXY_TARGET` 으로 바꾼다(docker 안에서는 `http://api:8000`).
- docker 의 `frontend` 컨테이너는 HMR 이 동작하지 않는다 — 수정 후 `docker compose restart frontend`.
- 브라우저 QA 는 `localhost` 말고 **`127.0.0.1`** 로 접속한다.

## 6. API 연동 규칙

기준 계약은 `docs/API-SPEC.md` 다. 계약 변경 PR 은 본문에 명시하고 백엔드와 공유한다.

- **모든 호출은 `src/lib/apiClient.ts` 를 경유한다.** 컴포넌트에서 `fetch` 를 직접 부르지 않는다.
- 뮤테이션(POST·PATCH·PUT·DELETE)은 `X-CSRF-Token` 을 자동으로 싣는다. CSRF 를 요구하지 않는
  예외는 `skipCsrf` 로 표시된 두 곳뿐이다 — `/api/auth/login`, `/api/matches/render-template`.
- **403 을 CSRF 만료로 단정하지 않는다.** 서버는 권한 부족에도 403 을 낸다. 토큰을 다시 받아
  **실제로 값이 달라졌을 때만** 재시도한다(`apiClient.csrf.test.ts` 가 회귀를 고정한다).
- 4xx 는 재시도하지 않는다(`queryClient.ts`). 뮤테이션은 재시도 자체를 끈다.
- 에러 `detail` 은 항상 문자열로 정규화해서 쓴다 — FastAPI 422 의 `detail` 은 배열이라
  `<p>{detail}</p>` 에 그대로 넣으면 React 가 크래시하고 화면이 백지가 된다.
- 409 는 차단 사유를 담고 온다(중복 답글·계정 삭제·사용자 삭제 가드). **`detail` 을 그대로
  사용자에게 보여준다** — "실패했습니다" 로 뭉개지 않는다.

## 7. 불변식

- **자동 게시 UI 를 만들지 않는다.** 발송은 사람이 승인 버튼을 눌렀을 때만 일어난다.
  자동 승인·타이머 승인·일괄 자동화 같은 경로를 프론트에서 만들지 않는다.
- 토큰·시크릿을 화면·콘솔·URL 에 찍지 않는다. 서버도 내려주지 않는다.

## 8. 작성 규칙

- 주석·커밋 메시지·PR 본문은 한국어.
- 브랜치 프리픽스: `feat/fe-*`, `fix/fe-*`, `docs/fe-*`. 타깃은 `dev`.
- 이슈를 해소하는 PR 은 본문에 `Closes #N` 을 넣는다.
