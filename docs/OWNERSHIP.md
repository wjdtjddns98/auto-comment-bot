# 프론트엔드 / 백엔드 분담표

> 2인 협업(FE/BE) 전체 소유·작업 분담. 근거: `CLAUDE.md` §역할분담, `.github/CODEOWNERS`.
> 원칙: **자기 영역만 직접 수정** · 상대 영역은 제안·PR 코멘트로만 · 계약(엔드포인트·스키마) 변경은 양쪽 공유(`docs/API-SPEC.md` 기준).

---

## 1. 파일 / 디렉터리 소유

| 경로 | 소유 | 내용 |
|---|---|---|
| `backend/**` | **BE** | 파이썬 전부 |
| `backend/app/models/` | BE | Tortoise 모델 |
| `backend/app/adapters/` | BE | SourceAdapter(Threads/Naver/Community) |
| `backend/app/crypto.py` | BE | Fernet 자격증명 암/복호 |
| `backend/app/auth*` | BE | 세션 쿠키 + CSRF |
| `backend/app/api/` | BE | REST 라우트·스키마 |
| `backend/migrations/` | BE | aerich 마이그레이션 |
| `backend/tests/` | BE | pytest |
| `frontend/**` | **FE** | React/Vite, `*.tsx` |
| `frontend/src/` | FE | 컴포넌트·페이지·상태관리·스타일 |
| `frontend/vite.config.ts`, `tsconfig*` | FE | 프론트 빌드/타입 설정 |
| `.github/**` | BE | CI·CODEOWNERS(인프라) |
| `.claude/`, `scripts/hooks/` | BE | 하네스(브랜치 가드) |
| `docker-compose.yml` | BE(api/db) · FE(frontend 서비스 블록 협의) | 공유 파일 — 변경 시 상대 공유 |
| `docs/API-SPEC.md` | **BE 확정 / FE 소비** | 계약. 변경은 양쪽 공유 |
| `docs/PRD.md`·`docs/ERD.md`·`docs/OWNERSHIP.md` | 공통 | |
| `CLAUDE.md`·`AGENTS.md` | 공통 | |

---

## 2. M1 작업 분담

### 백엔드 (`feat/be-*` 브랜치)
| 작업 | 우선순위 |
|---|---|
| crypto — Fernet 자격증명 암/복호(+MultiFernet 회전) | P0 |
| auth — 세션 쿠키 + CSRF | P0 |
| SourceAdapter ABC + Threads/Naver/Community 어댑터 | P0 |
| 키워드 매칭(substring/regex) | P1 |
| poller(APScheduler, `last_success_at` 백필 커서) | P1 |
| approve CAS 플로우(exactly-once) | P0 |
| REST 엔드포인트(API-SPEC 구현) | P0 |
| 테스트 게이트 5종(동시승인·토큰배제·백필·workers=1·id안정성) | P0 |

### 프론트엔드 (`feat/fe-*` 브랜치)
| 작업 | 우선순위 |
|---|---|
| 로그인 페이지 | P0 |
| 대시보드(매칭 리스트 + 필터 + 소스 헬스 배지) | P0 |
| 매칭 상세/승인(템플릿 선택·편집·approve, clipboard degrade) | P0 |
| 소스/키워드/템플릿/SNS계정 관리 화면 | P1 |
| 감사 로그 뷰 | P2 |
| TanStack Query 연동 + CSRF 토큰 처리 | P0 |

---

## 3. API 계약 경계 (BE 구현 / FE 소비)

모든 `/api/*` 엔드포인트: **BE가 구현, FE가 호출**. 상세 스키마는 `docs/API-SPEC.md`.

| 엔드포인트 | BE | FE |
|---|---|---|
| `/api/auth/*` (login·logout·me·csrf) | 구현 | 로그인/세션·CSRF 토큰 취득 |
| `/api/sources`·`/api/keywords`·`/api/templates` | 구현 | 관리 화면 |
| `/api/sns-accounts` (토큰 배제) | 구현 | 계정 등록 화면 |
| `/api/matches` (목록·상세) | 구현 | 대시보드·상세 |
| `/api/matches/{id}/approve` | 구현(CAS·전송) | 승인 UI + **409/502 처리** |
| `/api/matches/{id}/ignore`·`/retry` | 구현 | 버튼 |
| `/api/reply-actions` | 구현 | 감사 로그 뷰 |
| `/api/health` | 구현 | 소스 헬스 배지 |

> ⚠️ approve 응답 분기: `can_write=false`면 `clipboard_body`(수동 복사), true면 전송. FE가 이 분기를 UI로 처리.

---

## 4. 게이트 / CI 책임

| 영역 | 로컬 게이트 | CI 잡 |
|---|---|---|
| BE | `ruff check .` + `pytest` | `test`, `pg-migration` |
| FE | `npm run build`(tsc --noEmit + vite build) | `frontend-build` |
| 공통 | — | `ci-ok`(단일 required 게이트) |

path 필터로 BE PR은 FE 빌드를, FE PR은 BE 테스트를 서로 안 기다린다.

---

## 5. 경계 규칙

- **계약 변경**(엔드포인트·요청/응답 스키마)은 PR 본문에 명시 + 상대에게 공유. 기준 문서 = `docs/API-SPEC.md`.
- **상호 인간 코드리뷰는 안 한다**(서로 도메인 모름) — 각자 Claude로 리뷰 + self-merge. PR 승인 요구 0.
- 브랜치: `main`(동결) / `dev`(트렁크) / `prod`(배포). 작업은 dev 타깃 PR, 영역 프리픽스(`*-be`/`*-fe`).
- `main`/`prod` 직접 push 금지(훅 + 브랜치 보호 이중 방어).
