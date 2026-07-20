from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # postgres 접속 (docker-compose에서 주입)
    database_url: str = "postgres://sns:sns@db:5432/sns"
    app_env: str = "dev"

    # Notion 영구 자동 리포터. NOTION_TOKEN 이 비면 스케줄러가 잡을 등록 안 함.
    notion_token: str = ""
    git_repo_dir: str = "/repo"  # 컨테이너에 마운트된 저장소 루트(.git 포함)
    notion_report_db: str = "915726bfb5154593b5def3ea1cacc813"
    notion_scrum_db: str = "fe5c49f4a3fb40898ed983ab22e9e8e3"

    # ponytail: Fernet 키/외부 API 토큰은 sns_accounts 모델이 생기는 M1에서 추가.


settings = Settings()
