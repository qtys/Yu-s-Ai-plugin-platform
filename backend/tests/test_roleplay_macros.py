from app.roleplay_macros import MacroSession
from app.roleplay import preset_diagnostics


def test_nested_order_and_request_isolation():
    session = MacroSession({"char": "小雨", "user": "你"})
    warnings = []
    assert session.render("{{setvar::{{char}}_mood::你好，{{user}}}}{{getvar::小雨_mood}}", warnings) == "你好，你"
    assert session.render("{{getvar::小雨_mood}}", warnings) == "你好，你"
    assert MacroSession().render("{{getvar::小雨_mood}}", []) == ""
    assert not warnings


def test_invalid_blocks_no_side_effects_or_code_execution():
    session = MacroSession()
    warnings = []
    assert session.render("{{setvar::x::bad}}{{roll::100000d6}}", warnings) == ""
    assert session.variables == {}
    assert session.render("{{setvar::x::bad}}{{eval::print('evil')}}", warnings) == ""
    assert session.variables == {}
    assert session.render("{{// {{setvar::x::bad}} }}", warnings) == ""
    assert session.variables == {}
    assert session.render("{{setvar::x::a}}{{setvar x}}b{{/setvar}}", warnings) == ""
    assert session.variables == {}


def test_random_dice_and_variable_operations():
    text = "{{random::a::b::c}} {{roll:2d6+3}}"
    assert MacroSession(seed=42).render(text, []) == MacroSession(seed=42).render(text, [])
    session = MacroSession()
    assert session.render("{{setvar::n::1}}{{addvar::n::2}}{{incvar n}}/{{decvar n}}/{{hasvar n}}", []) == "4/3/true"
    assert session.render("{{deletevar n}}{{hasvar n}}", []) == "false"


def test_large_preset_diagnostics_and_disabled_variables():
    prompts = [{"identifier": str(i), "content": "{{setvar::x::value}}"} for i in range(178)]
    prompts.append({"identifier": "unsupported", "content": "{{getglobalvar::private}}"})
    order = [{"identifier": p["identifier"], "enabled": i < 57} for i, p in enumerate(prompts)]
    order.extend([{"identifier": "unsupported", "enabled": True}, {"identifier": "chatHistory", "enabled": True}])
    # Avoid duplicate order identifier while leaving unsupported enabled.
    order = [o for o in order[:-2] if o["identifier"] != "unsupported"] + order[-2:]
    report = preset_diagnostics({"prompts": prompts, "prompt_order": [{"character_id": 100001, "order": order}]})
    assert report["counts"]["ready"] == 57
    assert report["counts"]["disabled"] == 121
    assert report["counts"]["unsupported"] == 1
    assert report["counts"]["dynamic"] == 1
