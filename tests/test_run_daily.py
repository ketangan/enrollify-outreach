import importlib.util
import sys
from pathlib import Path

import pytest


def _load_script(module_name: str, script_name: str):
    module_path = Path(__file__).resolve().parents[1] / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_daily = _load_script("run_daily_for_tests", "run_daily.py")


def test_daily_stops_before_costly_phases_when_templates_are_stale(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["run_daily.py", "--skip-sync"])
    monkeypatch.setattr(run_daily, "run_phase", lambda script, extra: calls.append(script) or True)

    def stale_templates():
        raise RuntimeError("stale templates")

    monkeypatch.setattr(run_daily.brand_guard, "assert_templates_rebranded", stale_templates)

    with pytest.raises(SystemExit) as exc:
        run_daily.main()

    assert exc.value.code == 1
    assert calls == []


def test_daily_returns_nonzero_when_any_phase_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["run_daily.py", "--skip-sync", "--skip-owners"])
    monkeypatch.setattr(run_daily.brand_guard, "assert_templates_rebranded", lambda: None)

    def fake_run_phase(script, extra):
        calls.append(script)
        return script != "run_phase_6_followup.py"

    monkeypatch.setattr(run_daily, "run_phase", fake_run_phase)

    with pytest.raises(SystemExit) as exc:
        run_daily.main()

    assert exc.value.code == 1
    assert calls == ["run_phase_6_followup.py", "run_phase_5_drafts.py"]


def test_daily_stops_before_drafting_when_sync_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["run_daily.py"])
    monkeypatch.setattr(run_daily.brand_guard, "assert_templates_rebranded", lambda: None)

    def fake_run_phase(script, extra):
        calls.append(script)
        return False

    monkeypatch.setattr(run_daily, "run_phase", fake_run_phase)

    with pytest.raises(SystemExit) as exc:
        run_daily.main()

    assert exc.value.code == 1
    assert calls == ["run_phase_6_sync.py"]
