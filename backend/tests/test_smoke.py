"""네트워크/DB 없이 도는 스모크 테스트 — 모델 계약(ERD)을 고정한다.

CI 의 `test` 잡은 DB 없이 이걸 돌려 green 을 확보한다. DB 통합/동시성
테스트(테스트 게이트 5종)는 M1 진행 중 pg 잡에서 추가된다.
"""
from app import models


def test_app_main_imports():
    """앱 임포트 스모크 — 삭제된 모듈 참조 잔재를 **유닛 잡에서** 잡는다(#84 재발 방지).

    #81 이 `app/integrations/notion.py` 를 지웠는데 `main.py` 임포트가 남아 dev 가 red
    였다. 그때 이 잡(DB 불필요, `pytest -m "not db"`)은 **pass** 였다 — notion 테스트도
    함께 삭제돼 `app.main` 을 타는 경로가 없었기 때문이다. 그래서 실제로 앱을 임포트하는
    pg 잡만 실패해 증상이 한쪽에만 드러났고 원인 파악이 늦어졌다.
    (`import app.main` 은 lifespan 을 실행하지 않으므로 DB·네트워크가 필요 없다.)
    """
    import app.main  # noqa: F401 - 임포트 성공 자체가 검증 대상

    assert app.main.app is not None


def test_table_names():
    assert models.MatchedPost._meta.db_table == "matched_posts"
    assert models.SnsAccountSecret._meta.db_table == "sns_account_secrets"
    assert models.ReplyActionLog._meta.db_table == "reply_actions"


def test_post_status_has_sending_state():
    # 이중발송 CAS 의 중간 상태(MUST-FIX #1) 가 존재해야 한다.
    assert models.PostStatus.sending.value == "sending"


def test_reply_action_sent_value():
    # partial unique index 가 거는 값과 일치해야 한다.
    assert models.ReplyAction.sent.value == "sent"


def test_secret_is_separate_model():
    # 자격증명은 sns_accounts 가 아닌 별도 모델에 있어야 한다(MUST-FIX #3).
    account_fields = set(models.SnsAccount._meta.fields_map.keys())
    assert "encrypted_credentials" not in account_fields
