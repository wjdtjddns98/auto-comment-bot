from tortoise import BaseDBAsyncClient


# 2차 적대 리뷰 반영 — 마이그레이션 2번이 놓친 감사/관리 리소스 보호 2건:
# - keywords.source_scope: CASCADE → RESTRICT (소스 삭제가 스코프 키워드를 조용히 삭제 방지)
# - reply_actions.reviewer: CASCADE → RESTRICT (행위자 삭제로 감사 이력 증발 방지)
async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "keywords" DROP CONSTRAINT "keywords_source_scope_id_fkey";
        ALTER TABLE "keywords" ADD CONSTRAINT "keywords_source_scope_id_fkey"
            FOREIGN KEY ("source_scope_id") REFERENCES "sources" ("id") ON DELETE RESTRICT;
        ALTER TABLE "reply_actions" DROP CONSTRAINT "reply_actions_reviewer_id_fkey";
        ALTER TABLE "reply_actions" ADD CONSTRAINT "reply_actions_reviewer_id_fkey"
            FOREIGN KEY ("reviewer_id") REFERENCES "users" ("id") ON DELETE RESTRICT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "keywords" DROP CONSTRAINT "keywords_source_scope_id_fkey";
        ALTER TABLE "keywords" ADD CONSTRAINT "keywords_source_scope_id_fkey"
            FOREIGN KEY ("source_scope_id") REFERENCES "sources" ("id") ON DELETE CASCADE;
        ALTER TABLE "reply_actions" DROP CONSTRAINT "reply_actions_reviewer_id_fkey";
        ALTER TABLE "reply_actions" ADD CONSTRAINT "reply_actions_reviewer_id_fkey"
            FOREIGN KEY ("reviewer_id") REFERENCES "users" ("id") ON DELETE CASCADE;"""
