from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # postgres 접속 (docker-compose에서 주입)
    database_url: str = "postgres://sns:sns@db:5432/sns"
    # 허용 값 고정: 오타(예: "production")로 dev 보안 설정이 조용히 적용되는 것 방지.
    app_env: Literal["dev", "prod"] = "dev"

    # Notion 영구 자동 리포터. NOTION_TOKEN 이 비면 스케줄러가 잡을 등록 안 함.
    notion_token: str = ""
    git_repo_dir: str = "/repo"  # 컨테이너에 마운트된 저장소 루트(.git 포함)
    notion_report_db: str = "915726bfb5154593b5def3ea1cacc813"
    notion_scrum_db: str = "fe5c49f4a3fb40898ed983ab22e9e8e3"

    # 인증/암호화 (M1)
    # 세션 쿠키 서명·암호화 키(Fernet). dev 에서 비면 임시 키 자동 생성(app/auth.py).
    session_fernet_key: str = ""
    session_ttl_sec: int = 60 * 60 * 24 * 7  # 7일
    # SNS 자격증명 암호화 키(쉼표 구분, 첫 키=암호화·나머지=복호 전용 — 회전 경로 NFR-S2).
    credentials_fernet_keys: str = ""

    # poller (M1). tick 은 "폴링 주기 도래 판정" 주기 — 소스별 실제 주기는 poll_interval_sec.
    poller_enabled: bool = True
    poller_tick_sec: int = Field(default=30, ge=1)

    @property
    def cookie_secure(self) -> bool:
        return self.app_env != "dev"


settings = Settings()
