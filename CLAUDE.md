# sns댓글봇 — 작업 규칙

> SNS 키워드 모니터링 + **사람 승인형** 답변 웹앱(소셜 리스닝). 자동 게시 봇 아님.
> 상세 요구/설계는 `docs/PRD.md`·`docs/ERD.md`·`docs/API-SPEC.md` 참조.

## 하네스 비활성화 (이 프로젝트 한정)
- Fable 모델 사용 시 전역 하네스 지침 전부 무시: **oh-my-claudecode(OMC) 오케스트레이션·fablize
  operating mode/goals.py·ponytail 모드·RTK 프리픽스** — 이 프로젝트에서는 적용하지 않는다.
- `.claude/settings.json` 의 `enabledPlugins` 로 해당 플러그인도 프로젝트 수준에서 꺼져 있다.
- 이 파일의 프로젝트 규칙(역할 분담·안전 가드레일·불변식)은 그대로 유효하다.

## 역할 분담 (작업자 2인 — 프론트/백엔드) *(공통)*
> 이 파일은 백엔드·프론트 양쪽이 공유한다. 아래는 **영역 기준**이며, 각 담당(과 각자의 Claude)은
> **자기 영역만** 직접 수정한다("우리/너" 같은 1인칭 아님 — 읽는 쪽 기준으로 해석하지 말 것).
- **백엔드 영역(Python 전부)**: FastAPI 서버(라우트·스키마·인증)·source 어댑터(Threads/네이버/커뮤니티)·
  poller/스케줄러·키워드 매칭·DB/Tortoise 모델·aerich 마이그레이션·dedup·reply 플로우·테스트.
- **프론트엔드 영역(`frontend/**`)**: React/Vite, `*.tsx`, 컴포넌트·스타일·상태관리·프론트 빌드/타입체크.
- **각 담당은 상대 영역을 직접 수정하지 않는다** — 필요하면 제안·PR 코멘트로만(직접 커밋 금지).
- 경계: 대시보드/승인 UI는 **백엔드가 API**, **프론트가 React 컴포넌트**. FastAPI 서버 로직은 백엔드.

### 협업 워크플로 (dev 트렁크 + prod 배포)
- **`dev`=통합 트렁크(기본 브랜치), `prod`=배포, `main`=동결(잠금·히스토리 보존).** 모든 작업은
  dev 에서 짧은 브랜치 따서 PR → dev. 배포는 **dev→prod 승격 PR**(같은 `ci-ok` 게이트).
- 브랜치명 영역 프리픽스: 백엔드 `feat/be-*`·`fix/be-*`, 프론트 `feat/fe-*`(또는 `feat/web-*`).
- 작업 시작 전 `git checkout dev && git pull`. 변경은 **PR → green CI → squash 머지 → 브랜치 삭제**.
- 소유권은 `.github/CODEOWNERS`(`frontend/**`→프론트, 그 외→백엔드). **상호 인간 코드리뷰는 안 한다**
  — 각자 Claude 로 리뷰하고 self-merge. PR 승인 요구 0.
- CI: 영역 path 필터로 백엔드 잡(test·pg)과 프론트 잡(build)을 분리, 단일 게이트 `ci-ok` 하나만 통과.
- **FE↔BE 계약(엔드포인트·요청/응답 스키마) 변경은 PR 본문에 명시 + 상대에게 공유** — 기준은 `docs/API-SPEC.md`.
- dev·prod 브랜치 보호: **PR 필수 + `ci-ok` green 필수**(직접 push·force-push 차단).
- > ⚠️ 아직 git 저장소 아님. 이 워크플로는 `git init` + GitHub 리모트 + CI 설정 후 활성화된다.

## 0. 기본 동작 *(공통)*
- **소~중형 개발**(버그픽스·옵션 추가·멀티파일 수정) → 담당자가 직접 구현 + 리뷰어 1회 검증.
- **대형/보안민감**(신규 모듈·아키텍처·인증/시크릿/신뢰불가 입력) → 적대적 리뷰 2회.
- 범위 애매하면 한 번 확인, 합리적이면 추정·진행.
- 주석·커밋 메시지·PR 본문은 한국어.

## 1. 안전 가드레일 *(공통)*
- **dev·prod 직접 push 금지** — `feat/*`·`fix/*` 브랜치 + PR + green CI 경유.
- 하네스: `.claude/settings.json` PreToolUse 훅(`scripts/hooks/block-frozen-push.mjs`)이 **main/prod 직접
  push 를 도구 실행 전에 차단**한다(서버측 잠금·보호와 이중 방어). 우회 금지 — 막히면 dev 타깃 PR 로.
- 커밋 전 로컬 게이트 green 필수 — **백엔드**: `ruff check .` + `pytest -q` / **프론트**: `npm run build`(tsc+vite) + `npm test`(vitest).
- Conventional commit + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` 트레일러.

## 2. 백엔드 — 불변식(절대 위반 금지) *(백엔드 전용)*
- ① **자동 게시 금지.** 어떤 답변도 사람의 명시적 승인(approve 클릭) 없이 전송되지 않는다. 코드에 자동 발송 경로를 만들지 않는다.
- ② **이중 발송 금지.** 승인은 CAS 클레임(`status IN('new','reviewing')→'sending'`, 0행이면 abort) + `reply_actions(matched_post_id) WHERE action='sent'` partial unique index 로 **DB가 구조적으로** 보장한다.
- ③ **자격증명 노출 금지.** 토큰은 `sns_account_secrets` 별도 테이블 + Fernet 암호화. read path·API 응답·로그·트레이스백·URL 에 절대 노출 금지.
- ④ **예의 있는 수집.** 공식/공개 API(Threads API, 네이버 검색 OpenAPI, RSS)만. headless 스크레이핑 금지. 소스별 rate-limit + 429/403 backoff 준수.

## 3. 백엔드 — 운영 *(백엔드 전용)*
- uvicorn `--workers 1` 고정(in-process APScheduler 중복 실행 방지). CPU 작업은 `run_in_executor` offload.
- `docker compose up` 기동. DB=Postgres(호스트 5433), API=8000. 마이그레이션 aerich, forward-only.
- 도메인 모델=Tortoise ORM. Python 주석/docstring 한국어, ruff line-length=100(도입 시).
- 배포 전 pre-migration `pg_dump` 스냅샷. 백업은 off-host 복사.

## 4. 프론트엔드 *(프론트 전용 — 프론트 개발자가 관리)*
> 백엔드는 이 섹션을 채우거나 `frontend/**` 를 수정하지 않는다. 프론트 개발자가 관리한다.
> (세분화 필요 시 `frontend/CLAUDE.md` 를 두면 그 디렉터리에서 자동 적용된다.)
- 스택: React + TypeScript(Vite), 서버상태=TanStack Query(전역 스토어 YAGNI).
- 게이트: `npm run build`(tsc --noEmit + vite build) + `npm test`(vitest run) 둘 다 green.
  CI `프론트엔드 빌드 & 테스트` 잡이 두 개를 그대로 돌린다(PR #114) — 빌드만 통과시키고 올리면 CI 에서 잡힌다.
- API 연동: `docs/API-SPEC.md` 계약에 맞춰 호출. approve 등 write 요청은 CSRF 토큰(`X-CSRF-Token`) 포함. 계약 변경 PR 은 양쪽 공유.

## 5. 절대 추측 금지
- 마음대로 추측해서 답변하지 않는다. 모르면 확인하거나 근거를 제시한다.
