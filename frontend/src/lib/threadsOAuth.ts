// Threads OAuth 연동 왕복에서 페이지·모의 서버가 함께 쓰는 상수.
// 페이지 모듈이 아니라 여기에 두는 이유: mockServer 가 페이지를 import 하면
// pages → apiClient → mockServer 순환이 생긴다.

/** 동의 화면이 되돌아오는 앱 내 콜백 경로. 백엔드 `THREADS_REDIRECT_URI` 와 정확히 같아야 한다. */
export const THREADS_OAUTH_CALLBACK_PATH = "/oauth/threads/callback";

/** 전환 전 배포가 쓰는 외부 정적 콜백 페이지(`docs/app-review/threads-callback.html`).
 *  실서버 `THREADS_REDIRECT_URI` 의 현재 값이며, 모의 서버가 그 배포를 재현할 때만 쓴다.
 *  백엔드가 #82 로 앱 내 콜백으로 전환하면 이 상수와 수동 경로는 함께 정리 대상이다. */
export const THREADS_EXTERNAL_CALLBACK_URL = "https://nutti.co.kr/threads-callback.html";

/** 연동 시작 화면의 표시 이름을 콜백까지 나르는 sessionStorage 키.
 *  동의 화면 왕복은 전체 페이지 이동이라 컴포넌트 상태가 끊긴다.
 *  코드·state·토큰 등 자격증명은 절대 담지 않는다(불변식 ③). */
export const THREADS_OAUTH_DISPLAY_NAME_KEY = "threads-oauth-display-name";
