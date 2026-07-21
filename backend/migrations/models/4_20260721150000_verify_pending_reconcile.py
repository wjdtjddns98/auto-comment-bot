from tortoise import BaseDBAsyncClient


# M2 unknown-outcome 조정 설계(docs/M2-SEND-RECONCILIATION.md §3.1, 체크리스트 R1):
# - matched_posts.verify_meta: 조정 재료 JSONB (클레임 시 기록, 종결 시 null)
# - sns_accounts.platform_username: 조정 잡의 "우리 답글" 판정 키
# enum(status/action)은 VARCHAR 저장이라 값 추가에 DDL 이 필요 없다 — COMMENT 만 갱신.
async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "matched_posts" ADD COLUMN "verify_meta" JSONB;
        ALTER TABLE "sns_accounts" ADD COLUMN "platform_username" VARCHAR(255);
        COMMENT ON COLUMN "matched_posts"."status" IS 'new: new\nreviewing: reviewing\nsending: sending\nreplied: replied\nignored: ignored\nverify_pending: verify_pending';
        COMMENT ON COLUMN "reply_actions"."action" IS 'approved: approved\nsent: sent\nfailed: failed\ncanceled: canceled\nunknown: unknown';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "matched_posts" DROP COLUMN "verify_meta";
        ALTER TABLE "sns_accounts" DROP COLUMN "platform_username";
        COMMENT ON COLUMN "matched_posts"."status" IS 'new: new\nreviewing: reviewing\nsending: sending\nreplied: replied\nignored: ignored';
        COMMENT ON COLUMN "reply_actions"."action" IS 'approved: approved\nsent: sent\nfailed: failed\ncanceled: canceled';"""
