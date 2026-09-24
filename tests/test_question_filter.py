from alicia.client import question_is_real


def test_question_is_real():
    assert question_is_real("Which Salesforce org alias should we use for partial?")
    assert not question_is_real("<the missing input>")
    assert not question_is_real("<missing input>")
    assert not question_is_real("None")
    assert not question_is_real("short")
