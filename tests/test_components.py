"""Factual experience cards remain usable without motion or complete metrics."""

from streamlit.testing.v1 import AppTest


def _component_app():
    import streamlit as st
    from ui.components import render_market_pulse, render_debrief
    render_market_pulse(st.session_state.get("pulse"),animate=st.session_state.get("animate",True))
    render_debrief(st.session_state.get("debrief",{}))


def _render(pulse=None,debrief=None,animate=True):
    app=AppTest.from_function(_component_app)
    app.session_state["pulse"]=pulse
    app.session_state["debrief"]=debrief or {}
    app.session_state["animate"]=animate
    return app.run()


def _pulse(move=-.04):
    return {"count":5,"start_date":"2008-09-15","latest_date":"2008-09-19","latest_return":.01,
        "largest_abs_move":{"date":"2008-09-17","return":move},"costs":12.5,
        "headline":"Recorded portfolio change"}


def test_pulse_motion_bounded_reduced_motion_and_actual_new_move():
    app=_render(_pulse())
    assert not app.exception
    styles="\n".join(element.proto.body for element in app.get("html"))
    assert "ftm_pulse_drop_motion" in styles
    assert "1.6s ease-in-out 2" in styles
    assert "prefers-reduced-motion:reduce" in styles
    assert "infinite" not in styles
    assert any("-4.00%" in element.value and "17 Sep 2008" in element.value for element in app.markdown)
    assert any("USD 12.50" in element.value for element in app.caption)


def test_motion_disabled_neutral_and_untrusted_headline_escaped():
    pulse=_pulse(.04)
    pulse["headline"]="<script>untrusted()</script>"
    app=_render(pulse,animate=False)
    assert not app.exception
    styles="\n".join(element.proto.body for element in app.get("html"))
    assert "ftm_pulse_rise_still" in styles and "animation:none;" in styles
    assert "<script>" not in styles
    assert app.subheader[0].value=="&lt;script&gt;untrusted()&lt;/script&gt;"
    neutral=_render(_pulse(.0299))
    assert "ftm_pulse_steady_still" in neutral.get("html")[0].proto.body


def test_debrief_is_factual_and_handles_missing_metrics():
    report={"available":True,"as_of":"2008-09-19","observations":5,
        "weakest_day":{"date":"2008-09-17","return":-.04},
        "deepest_drawdown":{"date":"2008-09-17","magnitude":.1,"peak_date":"2008-09-15"},
        "recovery":{"status":"unrecovered","current_drawdown":.08},
        "cash_average_weight":.4,"cost_total":12.5,"cost_drag":.000125,"relative_return":.03}
    app=_render(debrief=report)
    assert not app.exception
    values="\n".join(element.value for element in app.markdown)
    assert "-4.00%" in "\n".join(element.value for element in app.subheader)
    assert "not been recovered" in values
    assert "+3.00 pp" in values
    assert "USD 12.50" in values
    empty=_render()
    assert not empty.exception
    assert any("at least one trading session" in element.value for element in empty.info)
    missing=_render(debrief={"available":True})
    assert not missing.exception
    assert "N/A" in "\n".join(element.value for element in missing.subheader)
