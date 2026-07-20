"""영구 자동 데일리 리포터 — git 로그를 읽어 Notion 데일리 DB에 항목을 만든다.

세션과 무관하게 도는 백엔드 잡(APScheduler가 매일 호출). headless 이므로
데이터 원천은 git 커밋뿐(대화 세션 메모리 접근 불가). NOTION_TOKEN 이 없으면
스케줄러가 이 잡을 등록하지 않는다.

수동 실행/점검:
    python -m app.integrations.notion --dry-run   # 게시 없이 payload 출력
    python -m app.integrations.notion             # 실제 게시(NOTION_TOKEN 필요)
"""
from __future__ import annotations

import subprocess
from datetime import date

import httpx

from app.config import settings

NOTION_API = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2022-06-28"


def get_commits(repo_dir: str, hours: int = 24) -> list[dict]:
    """최근 `hours` 시간 커밋을 [{hash, date, subject}] 로 반환."""
    # safe.directory=*: 마운트된 저장소가 다른 uid 소유여도 git 이 거부하지 않게
    out = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", repo_dir, "log",
         f"--since={hours} hours ago",
         "--pretty=format:%h\x1f%ad\x1f%s", "--date=short"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    commits = []
    for line in out.splitlines():
        if not line:
            continue
        h, d, s = line.split("\x1f", 2)
        commits.append({"hash": h, "date": d, "subject": s})
    return commits


def _area(commits: list[dict]) -> str:
    """커밋 스코프((be)/(fe))로 영역 추정. 'feat' 안의 'fe' 오탐을 피한다."""
    subjects = " ".join(c["subject"] for c in commits)
    has_be = "(be)" in subjects or "backend" in subjects
    has_fe = "(fe)" in subjects or "(web)" in subjects or "front" in subjects
    if has_be and has_fe:
        return "공통"
    return "프론트" if has_fe and not has_be else "백엔드"


def _rt(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text[:1900] or "-"}}]}


def build_report(commits: list[dict], today: str) -> dict:
    done = " · ".join(f"{c['hash']} {c['subject']}" for c in commits) or "커밋 없음"
    return {
        "제목": {"title": [{"text": {"content": f"{today} 리포트"}}]},
        "날짜": {"date": {"start": today}},
        "작성자": {"select": {"name": _area(commits)}},
        "영역": {"select": {"name": _area(commits)}},
        "완료": _rt(done),
        "진행중": _rt("-"),
        "블로커": _rt("-"),
        "다음": _rt("-"),
        "PR/커밋": {"url": f"https://github.com/N-utti/SNS-bot/commits?since={today}"},
    }


def build_scrum(commits: list[dict], today: str) -> dict:
    yday = " · ".join(c["subject"] for c in commits) or "커밋 없음"
    return {
        "제목": {"title": [{"text": {"content": f"{today} 스크럼"}}]},
        "날짜": {"date": {"start": today}},
        "작성자": {"select": {"name": _area(commits)}},
        "어제 한 일": _rt(yday),
        "오늘 할 일": _rt("-"),
        "블로커": _rt("-"),
    }


def _post(db_id: str, properties: dict) -> None:
    r = httpx.post(
        NOTION_API,
        headers={
            "Authorization": f"Bearer {settings.notion_token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        json={"parent": {"database_id": db_id}, "properties": properties},
        timeout=30,
    )
    r.raise_for_status()


def post_daily(dry_run: bool = False) -> None:
    """오늘자 스크럼 + 리포트를 Notion 에 생성. dry_run 이면 출력만."""
    today = date.today().isoformat()
    commits = get_commits(settings.git_repo_dir)
    report = build_report(commits, today)
    scrum = build_scrum(commits, today)
    if dry_run:
        import json
        print(f"[dry-run] commits={len(commits)}")
        print(json.dumps({"scrum": scrum, "report": report}, ensure_ascii=False, indent=2))
        return
    _post(settings.notion_scrum_db, scrum)
    _post(settings.notion_report_db, report)


if __name__ == "__main__":
    import sys
    post_daily(dry_run="--dry-run" in sys.argv)
