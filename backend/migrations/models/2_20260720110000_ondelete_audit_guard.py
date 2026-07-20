from tortoise import BaseDBAsyncClient


# 감사/이력 보호 (append-only 훼손 방지): 관리 리소스 삭제가 matched_posts·reply_actions 를
# 연쇄 삭제하지 못하게 ON DELETE 를 조정한다.
# - matched_posts.matched_keyword / reply_actions.template·sns_account: CASCADE → SET NULL
#   (참조 대상이 지워져도 이력 행은 남는다)
# - matched_posts.source / reply_actions.matched_post: CASCADE → RESTRICT
#   (이력이 딸린 부모는 삭제 불가 — 소스는 enabled=false 비활성화가 정식 경로)
async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "matched_posts" DROP CONSTRAINT "matched_posts_matched_keyword_id_fkey";
        ALTER TABLE "matched_posts" ADD CONSTRAINT "matched_posts_matched_keyword_id_fkey"
            FOREIGN KEY ("matched_keyword_id") REFERENCES "keywords" ("id") ON DELETE SET NULL;
        ALTER TABLE "matched_posts" DROP CONSTRAINT "matched_posts_source_id_fkey";
        ALTER TABLE "matched_posts" ADD CONSTRAINT "matched_posts_source_id_fkey"
            FOREIGN KEY ("source_id") REFERENCES "sources" ("id") ON DELETE RESTRICT;
        ALTER TABLE "reply_actions" DROP CONSTRAINT "reply_actions_matched_post_id_fkey";
        ALTER TABLE "reply_actions" ADD CONSTRAINT "reply_actions_matched_post_id_fkey"
            FOREIGN KEY ("matched_post_id") REFERENCES "matched_posts" ("id") ON DELETE RESTRICT;
        ALTER TABLE "reply_actions" DROP CONSTRAINT "reply_actions_sns_account_id_fkey";
        ALTER TABLE "reply_actions" ADD CONSTRAINT "reply_actions_sns_account_id_fkey"
            FOREIGN KEY ("sns_account_id") REFERENCES "sns_accounts" ("id") ON DELETE SET NULL;
        ALTER TABLE "reply_actions" DROP CONSTRAINT "reply_actions_template_id_fkey";
        ALTER TABLE "reply_actions" ADD CONSTRAINT "reply_actions_template_id_fkey"
            FOREIGN KEY ("template_id") REFERENCES "reply_templates" ("id") ON DELETE SET NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "matched_posts" DROP CONSTRAINT "matched_posts_matched_keyword_id_fkey";
        ALTER TABLE "matched_posts" ADD CONSTRAINT "matched_posts_matched_keyword_id_fkey"
            FOREIGN KEY ("matched_keyword_id") REFERENCES "keywords" ("id") ON DELETE CASCADE;
        ALTER TABLE "matched_posts" DROP CONSTRAINT "matched_posts_source_id_fkey";
        ALTER TABLE "matched_posts" ADD CONSTRAINT "matched_posts_source_id_fkey"
            FOREIGN KEY ("source_id") REFERENCES "sources" ("id") ON DELETE CASCADE;
        ALTER TABLE "reply_actions" DROP CONSTRAINT "reply_actions_matched_post_id_fkey";
        ALTER TABLE "reply_actions" ADD CONSTRAINT "reply_actions_matched_post_id_fkey"
            FOREIGN KEY ("matched_post_id") REFERENCES "matched_posts" ("id") ON DELETE CASCADE;
        ALTER TABLE "reply_actions" DROP CONSTRAINT "reply_actions_sns_account_id_fkey";
        ALTER TABLE "reply_actions" ADD CONSTRAINT "reply_actions_sns_account_id_fkey"
            FOREIGN KEY ("sns_account_id") REFERENCES "sns_accounts" ("id") ON DELETE CASCADE;
        ALTER TABLE "reply_actions" DROP CONSTRAINT "reply_actions_template_id_fkey";
        ALTER TABLE "reply_actions" ADD CONSTRAINT "reply_actions_template_id_fkey"
            FOREIGN KEY ("template_id") REFERENCES "reply_templates" ("id") ON DELETE CASCADE;"""
