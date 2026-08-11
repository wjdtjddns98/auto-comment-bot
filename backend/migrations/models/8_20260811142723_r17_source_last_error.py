from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sources" ADD "last_error_at" TIMESTAMPTZ;
        ALTER TABLE "sources" ADD "last_error" VARCHAR(500);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sources" DROP COLUMN "last_error_at";
        ALTER TABLE "sources" DROP COLUMN "last_error";"""
