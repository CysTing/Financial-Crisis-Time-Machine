"""Verify the bytes registered by real Streamlit download widgets can be saved.

AppTest uses Streamlit's in-memory media storage. Capture its public storage
method to inspect the generated attachment, rather than rebuilding a report in
the test. This complements browser checks without automating another browser.
"""

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "app.py"
FILES = {
    "financial-time-machine-report.html", "financial-time-machine-replay.csv",
    "financial-time-machine-trades.csv", "financial-time-machine-decisions.csv",
    "financial-time-machine-passport.json",
}


def clean(app):
    assert not app.exception, "\n".join(str(item.value) for item in app.exception)
    return app


def button(app, label):
    return next(item for item in app.button if item.label == label)


def capture_attachments(monkeypatch):
    attachments = {}
    original = MemoryMediaFileStorage.load_and_get_id

    def capture(storage, path_or_data, mimetype, kind, filename=None):
        identifier = original(storage, path_or_data, mimetype, kind, filename)
        if filename:
            assert Path(filename).name == filename
            media = storage.get_file(identifier)
            attachments[filename] = (media.content, media.mimetype)
        return identifier

    monkeypatch.setattr(MemoryMediaFileStorage, "load_and_get_id", capture)
    return attachments


def save_and_reopen(app, attachments, folder, completed):
    assert set(attachments) == FILES
    assert len(app.download_button) == 5
    assert all("/mock/media/" in item.proto.url for item in app.download_button)
    folder.mkdir()
    sim = app.session_state["survival"]
    clock, ledger = sim.current_date, sim.ledger.copy()
    for filename, (content, mimetype) in attachments.items():
        path = folder / filename
        path.write_bytes(content)
        assert path.read_bytes() == content and content
        text = path.read_text(encoding="utf-8")
        assert "synthetic" in text.lower()
        if filename.endswith(".csv"):
            frame = pd.read_csv(path)
            assert frame.as_of.eq(clock.strftime("%Y-%m-%d")).all()
            assert frame.report_status.str.startswith("Completed" if completed else "Partial").all()
            assert mimetype == "text/csv"
        elif filename.endswith(".json"):
            passport = json.loads(text)
            assert pd.Timestamp(passport["as_of"]) == clock
            assert passport["complete"] is completed
            assert passport["integrity"]["status"] == "pass"
            assert mimetype == "application/json"
        else:
            assert text.lstrip().lower().startswith("<!doctype html>")
            assert "<script" not in text.lower()
            assert ("DID YOUR PORTFOLIO SURVIVE THE CRISIS?" in text) is completed
            assert mimetype == "text/html"
    for download in list(app.download_button):
        app.download_button(key=download.key).click().run() if download.key else next(
            item for item in app.download_button if item.label == download.label).click().run()
        clean(app)
        assert sim.current_date == clock
        pd.testing.assert_frame_equal(sim.ledger, ledger)


def test_widget_attachment_bytes_save_reopen_complete_and_start_another_attempt(tmp_path, monkeypatch):
    attachments = capture_attachments(monkeypatch)
    app = clean(AppTest.from_file(str(APP), default_timeout=180).run())
    app.button(key="start_showcase").click().run()
    clean(app)
    original = app.session_state["survival"]
    attachments.clear()
    app.radio(key="page").set_value("Final Performance Report").run()
    clean(app)
    save_and_reopen(app, attachments, tmp_path / "partial", False)

    original.run()
    attachments.clear()
    clean(app.run(timeout=180))
    save_and_reopen(app, attachments, tmp_path / "completed", True)

    app.radio(key="page").set_value("Mission Control").run()
    app.radio(key="mission_source").set_value("Synthetic offline demonstration")
    app.selectbox(key="mission_scenario").set_value("inflation")
    app.selectbox(key="mission_strategy").set_value("Buy and Hold")
    app.run()
    button(app, "Initialize mission →").click().run()
    clean(app)
    button(app, "Launch validated mission →").click().run()
    clean(app)
    assert app.session_state["survival"] is not original
    assert len(app.session_state["survival"].history) == 1
    attachments.clear()
    app.radio(key="page").set_value("Final Performance Report").run()
    clean(app)
    save_and_reopen(app, attachments, tmp_path / "new-attempt", False)
    assert pd.Timestamp(json.loads((tmp_path / "new-attempt/financial-time-machine-passport.json").read_text())["as_of"]) != original.current_date


def test_research_recovers_oversized_legacy_inputs_without_touching_replay():
    app = clean(AppTest.from_file(str(APP), default_timeout=180).run())
    app.button(key="start_showcase").click().run()
    clean(app)
    sim = app.session_state["survival"]
    history, ledger, pending = sim.history.copy(), sim.ledger.copy(), sim.pending
    app.radio(key="page").set_value("Strategy Laboratory").run()
    app.session_state["lab_capital"] = 1e308
    app.session_state["lab_result"] = {"capital": 1e308}
    clean(app.run())
    assert app.number_input(key="lab_capital").value == 100000.
    assert "lab_result" not in app.session_state
    assert any("supported capital range" in item.value for item in app.warning)
    pd.testing.assert_frame_equal(sim.history, history)
    pd.testing.assert_frame_equal(sim.ledger, ledger)
    assert sim.pending == pending


def test_upper_bound_research_registers_finite_download_without_changing_replay(monkeypatch):
    attachments = capture_attachments(monkeypatch)
    app = clean(AppTest.from_file(str(APP), default_timeout=180).run())
    app.button(key="start_showcase").click().run()
    clean(app)
    sim = app.session_state["survival"]
    history, ledger = sim.history.copy(), sim.ledger.copy()
    app.radio(key="page").set_value("Strategy Laboratory").run()
    app.radio(key="lab_source").set_value("Synthetic offline demonstration")
    app.number_input(key="lab_capital").set_value(1e9)
    app.number_input(key="lab_cost").set_value(100.)
    app.run()
    attachments.clear()
    button(app, "Run comparative research →").click().run(timeout=180)
    clean(app)
    content, mimetype = attachments["strategy-comparison.csv"]
    frame = pd.read_csv(io.StringIO(content.decode("utf-8")))
    assert len(frame) == 6 and mimetype == "text/csv"
    numeric = frame.select_dtypes(include="number")
    assert np.isfinite(numeric.to_numpy()).all()
    assert app.session_state["lab_result"]["capital"] == 1e9
    pd.testing.assert_frame_equal(sim.history, history)
    pd.testing.assert_frame_equal(sim.ledger, ledger)
