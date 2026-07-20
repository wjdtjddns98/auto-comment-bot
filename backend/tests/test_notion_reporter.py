"""Notion 리포터 빌더 테스트 — 네트워크/git 없이 payload 구조만 검증."""
from app.integrations import notion


def test_build_report_shape():
    commits = [{"hash": "abc123", "date": "2026-07-08", "subject": "feat(be): x"}]
    p = notion.build_report(commits, "2026-07-08")
    assert p["제목"]["title"][0]["text"]["content"] == "2026-07-08 리포트"
    assert p["날짜"]["date"]["start"] == "2026-07-08"
    assert "abc123" in p["완료"]["rich_text"][0]["text"]["content"]


def test_build_scrum_shape():
    p = notion.build_scrum([], "2026-07-08")
    assert p["제목"]["title"][0]["text"]["content"] == "2026-07-08 스크럼"
    # 커밋 없으면 '커밋 없음' 표기
    assert p["어제 한 일"]["rich_text"][0]["text"]["content"] == "커밋 없음"


def test_area_inference():
    assert notion._area([{"hash": "1", "date": "d", "subject": "feat(be): x"}]) == "백엔드"
    assert notion._area([{"hash": "1", "date": "d", "subject": "feat(fe): x"}]) == "프론트"
    assert notion._area([
        {"hash": "1", "date": "d", "subject": "feat(be): x"},
        {"hash": "2", "date": "d", "subject": "feat(fe): y"},
    ]) == "공통"


def test_rich_text_truncates_and_defaults():
    assert notion._rt("")["rich_text"][0]["text"]["content"] == "-"
    assert len(notion._rt("x" * 5000)["rich_text"][0]["text"]["content"]) <= 1900
