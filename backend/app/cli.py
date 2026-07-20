"""운영 CLI — 최초 사용자 생성.

사용: python -m app.cli create-user --email a@b.c --role admin
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-user", help="사용자 생성")
    p.add_argument("--email", required=True)
    p.add_argument("--role", choices=[r.value for r in Role], default=Role.reviewer.value)
    args = parser.parse_args()

    password = getpass.getpass("비밀번호: ")
    if not password:
        parser.error("비밀번호는 비울 수 없습니다")
    if password != getpass.getpass("비밀번호 확인: "):
        parser.error("비밀번호가 일치하지 않습니다")
    asyncio.run(_create_user(args.email, password, Role(args.role)))


if __name__ == "__main__":
    main()
