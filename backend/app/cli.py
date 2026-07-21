"""운영 CLI — 최초 사용자 생성 + R3 실측 프로브.

사용:
  python -m app.cli create-user --email a@b.c --role admin
  python -m app.cli threads-probe-republish --account-id 1 --reply-to <media_id> --text "..."
비밀번호는 항상 프롬프트로만 입력받는다(--password 옵션 없음 —
셸 히스토리·프로세스 목록에 평문이 남는 경로 자체를 제거).
"""
import argparse
import asyncio
import getpass

from tortoise import Tortoise

from app.auth import hash_password
from app.db import TORTOISE_ORM
from app.models import Role, User


async def _create_user(email: str, password: str, role: Role) -> None:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        user = await User.create(email=email, password_hash=hash_password(password), role=role)
        print(f"생성됨: id={user.id} email={user.email} role={user.role.value}")
    finally:
        await Tortoise.close_connections()


async def _probe_republish(account_id: int, reply_to: str, text: str) -> None:
    from app.sources.threads import probe_republish

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        await probe_republish(account_id, reply_to, text)
    finally:
        await Tortoise.close_connections()


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-user", help="사용자 생성")
    p.add_argument("--email", required=True)
    p.add_argument("--role", choices=[r.value for r in Role], default=Role.reviewer.value)
    # R3 실측(M2 조정 설계 §4): 발행된 컨테이너 재발행 동작 관찰 — 실제 답글 1건이
    # 게시되므로 테스트 계정/게시물로만 실행할 것. 사람이 명시 실행하는 경로다(불변식 ①).
    pr = sub.add_parser(
        "threads-probe-republish",
        help="R3 실측: 발행된 컨테이너에 threads_publish 재호출 동작 관찰(실게시 1건 발생)",
    )
    pr.add_argument("--account-id", type=int, required=True)
    pr.add_argument("--reply-to", required=True, help="답글을 달 대상 Threads media id")
    pr.add_argument("--text", default="R3 재발행 실측 프로브")
    args = parser.parse_args()

    if args.command == "threads-probe-republish":
        asyncio.run(_probe_republish(args.account_id, args.reply_to, args.text))
        return

    password = getpass.getpass("비밀번호: ")
    if not password:
        parser.error("비밀번호는 비울 수 없습니다")
    if password != getpass.getpass("비밀번호 확인: "):
        parser.error("비밀번호가 일치하지 않습니다")
    asyncio.run(_create_user(args.email, password, Role(args.role)))


if __name__ == "__main__":
    main()
