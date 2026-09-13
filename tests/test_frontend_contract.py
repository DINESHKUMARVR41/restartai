from pathlib import Path

JS = Path(__file__).parents[1].joinpath("app","static","app.js").read_text(encoding="utf-8")
HTML = Path(__file__).parents[1].joinpath("app","templates","index.html").read_text(encoding="utf-8")

def test_frontend_defines_inline_actions():
    for fn in ["startRecovery","replan","approve","getAIRecommendation","downloadReport","testLiveCall","sendChat","updateModeUI"]:
        assert f"function {fn}" in JS

def test_frontend_helpers_are_defined():
    for fn in ["esc","safeText","money","readJsonResponse","showError","clearError"]:
        assert f"function {fn}" in JS

def test_live_start_does_not_send_blank_suppliers():
    assert ")).filter(s=>s.phone)" in JS

def test_overrides_are_optional():
    assert 'required_technicians_override:techOverride?Number(techOverride):null' in JS
    assert 'installation_minutes_override:installOverride?Number(installOverride):null' in JS

def test_test_call_route_is_preserved():
    assert '"/api/calle/test-call"' in JS
