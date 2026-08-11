"""템플릿 렌더링 유닛 — 랜덤 변형·매칭 변수·에러 처리(DB/네트워크 없이 돈다)."""
import random

import pytest

from app.templating import ALLOWED_VARS, TemplateRenderError, render

CTX = {"author": "prayforyou_x", "keyword": "간식", "url": "https://threads.com/p/1"}


def test_variables_substituted():
    out = render("{{author}}님, {{keyword}} 관련 정보예요 → {{url}}", CTX)
    assert out == "prayforyou_x님, 간식 관련 정보예요 → https://threads.com/p/1"


def test_variation_picks_one_candidate():
    """{{a|b|c}} 는 후보 중 하나로 치환된다 — 어떤 것이 나오든 후보 집합 안이어야 한다."""
    for _ in range(30):
        out = render("{{안녕하세요|반갑습니다|하이}} 치니!", CTX)
        assert out in ("안녕하세요 치니!", "반갑습니다 치니!", "하이 치니!")


def test_variation_differs_across_renders_with_seeded_rng():
    """같은 템플릿이라도 건마다 다른 문구가 나올 수 있어야 한다 — 일괄 발송에서 N건
    동일 문구(중복 콘텐츠 스팸 신호)를 피하는 것이 이 기능의 목적이다."""
    body = "{{a|b|c|d|e}}-{{f|g|h|i|j}}"
    outs = {render(body, CTX, rng=random.Random(seed)) for seed in range(20)}
    assert len(outs) > 1, "변형이 갈리지 않는다 — 스팸 완화 효과 없음"


def test_seeded_rng_is_deterministic():
    body = "{{하나|둘|셋}} {{author}}"
    assert render(body, CTX, rng=random.Random(7)) == render(body, CTX, rng=random.Random(7))


def test_empty_variation_candidate_allowed():
    """빈 후보는 "있거나 없거나" 를 표현한다. 후보의 앞뒤 공백은 서식이라 strip 된다 —
    `{{ 안녕 | 반가워 }}` 처럼 가독성용 공백을 넣어도 문구에 새지 않는다."""
    outs = {render("고마워요{{ !|}}", CTX, rng=random.Random(s)) for s in range(20)}
    assert outs <= {"고마워요!", "고마워요"}
    assert render("{{ 안녕 | 안녕 }} 치니", CTX) == "안녕 치니"


def test_unknown_variable_raises():
    with pytest.raises(TemplateRenderError) as exc:
        render("{{authorr}} 님", CTX)
    # 오타를 조용히 비우고 게시하는 것이 더 나쁘다 — 사용 가능 목록을 안내한다
    assert "authorr" in str(exc.value)
    for name in ALLOWED_VARS:
        assert name in str(exc.value)


def test_missing_value_raises():
    with pytest.raises(TemplateRenderError) as exc:
        render("{{author}} 님", {"author": None, "keyword": "간식", "url": None})
    assert "author" in str(exc.value)


def test_content_variable_not_allowed():
    """원문 복사는 어느 플랫폼에서도 스팸 신호라 변수로 제공하지 않는다."""
    assert "content" not in ALLOWED_VARS
    with pytest.raises(TemplateRenderError):
        render("{{content}}", CTX)


def test_plain_body_unchanged():
    assert render("변수 없는 평범한 문구입니다.", CTX) == "변수 없는 평범한 문구입니다."


def test_newlines_preserved():
    out = render("첫 줄\n\n{{author}} 님", CTX)
    assert out == "첫 줄\n\nprayforyou_x 님"
