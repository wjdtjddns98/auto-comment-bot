from tortoise import BaseDBAsyncClient


# 소스 표시용 이름(예: 커뮤니티 이름) — 선택 필드, 기존 행은 NULL(FE 가 config 로 폴백).
async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sources" ADD COLUMN "name" VARCHAR(100);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sources" DROP COLUMN "name";"""
