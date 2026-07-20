"""운영 CLI — 최초 사용자 생성.

사용: python -m app.cli create-user --email a@b.c --role admin [--password ...]
--password 생략 시 프롬프트로 입력(셸 히스토리에 비밀번호를 남기지 않는 권장 경로).
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
    p.add_argument("--password", default=None)
    p.add_argument("--role", choices=[r.value for r in Role], default=Role.reviewer.value)
    args = parser.parse_args()

    password = args.password or getpass.getpass("비밀번호: ")
    asyncio.run(_create_user(args.email, password, Role(args.role)))


if __name__ == "__main__":
    main()
