from tortoise import BaseDBAsyncClient


# Threads OAuth 연동(upsert)의 안정 식별자 컬럼 + 중복 연동 구조 차단.
# - platform_user_id: Meta 안정 user id. 수동 토큰 등록 행은 null(부분 unique 미적용).
# - 부분 unique (user_id, platform, platform_user_id): 같은 사용자가 같은 플랫폼 신원을
#   두 계정 행으로 갖는 상태를 DB 가 구조적으로 막는다(OAuth 1차 적대 리뷰 High-4 —
#   advisory lock 의 백스톱).
async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sns_accounts" ADD COLUMN "platform_user_id" VARCHAR(64);
        CREATE UNIQUE INDEX "uid_sns_accounts_identity"
            ON "sns_accounts" ("user_id", "platform", "platform_user_id")
            WHERE "platform_user_id" IS NOT NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX "uid_sns_accounts_identity";
        ALTER TABLE "sns_accounts" DROP COLUMN "platform_user_id";
    """
