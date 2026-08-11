"""답변 템플릿 렌더링 — 일괄 발송 시 **문구를 건마다 갈라주기 위한** 치환기.

일괄 발송(FE BulkSendPanel)이 N건에 완전히 동일한 문구를 보내면 rate-limit 과 무관하게
**중복 콘텐츠로 스팸 판정**될 수 있다(불변식 ④의 "예의 있는 수집" 과 같은 취지 — 게시
쪽에도 같은 절제가 필요하다). 그래서 템플릿에 두 가지 치환을 넣는다:

1. **랜덤 변형** `{{a|b|c}}` — 후보 중 하나를 고른다. 같은 템플릿으로 여러 건을 보내도
   문구가 갈린다. 후보는 사람이 직접 쓴 것이라 어색한 문장이 생기지 않는다.
2. **매칭 변수** `{{author}}`·`{{keyword}}`·`{{url}}` — 그 글에 맞는 값으로 개인화한다.

렌더링은 **발송이 아니다**: 서버가 문구만 만들어 돌려주고, 사람이 미리보기에서 확인한 뒤
approve 를 눌러야 전송된다(불변식 ①). 감사 로그에는 실제 전송된 문구가 그대로 남는다.

미지 변수(`{{typo}}`)는 조용히 비우지 않고 **에러로 돌려준다** — 오타가 그대로 게시되는
것이 더 나쁘기 때문이다. 값이 없는 매칭(예: author 미확보)도 같은 이유로 에러다.
"""
import random
import re

# {{...}} 한 덩어리. 중첩·개행은 지원하지 않는다(단순함이 목적 — 필요해지면 그때).
_TOKEN = re.compile(r"\{\{([^{}]*)\}\}")

# 매칭에서 뽑아 쓸 수 있는 변수. 원문 전체(content)는 의도적으로 제외한다 —
# 답글에 상대 글을 그대로 복사해 넣는 것은 어느 플랫폼에서도 스팸 신호다.
ALLOWED_VARS = ("author", "keyword", "url")


class TemplateRenderError(Exception):
    """치환 실패 — 미지 변수 또는 그 매칭에서 값을 얻을 수 없는 변수."""


def render(body: str, context: dict[str, str | None], *, rng: random.Random | None = None) -> str:
    """템플릿 본문을 렌더링한다.

    `context` 는 `ALLOWED_VARS` 키의 값(없으면 None). `rng` 를 주면 변형 선택이
    결정적이라 테스트가 재현 가능하다.
    """
    picker = rng or random
    missing: list[str] = []
    unknown: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        raw = m.group(1).strip()
        if "|" in raw:
            # 랜덤 변형: 빈 후보도 허용한다(예: "{{안녕하세요|}}" = 있거나 없거나)
            choices = [c.strip() for c in raw.split("|")]
            return picker.choice(choices)
        if raw not in ALLOWED_VARS:
            unknown.append(raw)
            return ""
        value = context.get(raw)
        if not value:
            missing.append(raw)
            return ""
        return value

    rendered = _TOKEN.sub(_sub, body)
    if unknown:
        names = ", ".join(sorted(set(unknown)))
        allowed = ", ".join(ALLOWED_VARS)
        raise TemplateRenderError(
            f"알 수 없는 템플릿 변수: {names} (사용 가능: {allowed} · 변형은 {{{{a|b}}}} 형식)"
        )
    if missing:
        names = ", ".join(sorted(set(missing)))
        raise TemplateRenderError(f"이 매칭에서 값을 얻을 수 없는 변수: {names}")
    # 변형·치환으로 생긴 연속 공백만 정리한다(줄바꿈은 사람이 의도한 것이라 보존).
    return re.sub(r"[ \t]{2,}", " ", rendered).strip()
