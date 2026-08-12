"""운영 설정 assert — 테스트 게이트 ④: uvicorn 단일 워커 (NFR-P1)."""
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_uvicorn_single_worker():
    """--workers 옵션 부재 = uvicorn 기본 1 워커 (in-process 스케줄러 중복 실행 방지)."""
    content = DOCKERFILE.read_text(encoding="utf-8")
    cmd_lines = [line for line in content.splitlines() if line.startswith("CMD")]
    assert len(cmd_lines) == 1 and "uvicorn" in cmd_lines[0]
    assert "--workers" not in cmd_lines[0]
