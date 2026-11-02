"""End-to-end tests on simulated hardware (no instruments needed).

Run with:  python -m pytest tests
"""

import threading

import numpy as np
import pytest

from fls.core.io import load_phasemeter_csv
from fls.core.processing import analyse_measurement, extract_steps, load_calibration
from fls.core.scope_analysis import analyse_frame
from fls.core.sequence import CALIBRATION, MEASUREMENT, AcquisitionSequence, Aborted, \
    RunRequest, SequenceCallbacks
from fls.core.storage import MeasurementFolder, safe_name
from fls.hardware import simulated
from fls.hardware.simulated import SimMokuDevice, SimWavegen, synthesize_stream
from fls.settings import Settings


@pytest.fixture
def settings():
    s = Settings()
    s.device.simulate = True
    s.phasemeter.stream_extra_s = 5
    return s


def test_steps_reject_glitches(tmp_path, settings):
    p = tmp_path / "m.csv"
    synthesize_stream(p, 12e-9, settings.sweep, 130, seed=1)
    raw = load_phasemeter_csv(p)
    steps = extract_steps(raw, settings.analysis)
    assert len(steps) > 3000
    assert steps.stats["rejected_outlier_samples"] > 0
    # all +64 cycle glitches removed: step phases follow a smooth curve
    assert np.max(np.abs(np.diff(steps.phase))) < 0.01


def test_calibration_and_measurement_recover_delay(tmp_path, settings):
    cal_p, meas_p = tmp_path / "CAL_1.csv", tmp_path / "m.csv"
    synthesize_stream(cal_p, simulated.SIM_INTERNAL_DELAY_S, settings.sweep, 130, seed=2)
    synthesize_stream(meas_p, simulated.SIM_INTERNAL_DELAY_S + 10e-9, settings.sweep, 130, seed=3)
    cal = load_calibration(cal_p, settings.analysis)
    res = analyse_measurement(load_phasemeter_csv(meas_p), cal, settings.analysis)
    assert res.delay_s == pytest.approx(10e-9, abs=5e-12)
    assert res.length_m == pytest.approx(10e-9 * settings.analysis.velocity_m_s, rel=1e-3)
    assert not res.warnings


def test_scope_phase_difference():
    f, delay = 1e6, 8e-9
    rng = np.random.default_rng(0)
    t = np.linspace(-1.5e-6, 1.5e-6, 4096)
    y1 = 0.2 + 0.1 * np.cos(2 * np.pi * f * t + 0.3) + rng.normal(0, 2e-3, t.size)
    y2 = 0.2 + 0.1 * np.cos(2 * np.pi * f * (t - delay) + 0.3) + rng.normal(0, 2e-3, t.size)
    a = analyse_frame(t, y1, y2)
    assert a.frequency_hz == pytest.approx(f, rel=1e-3)
    assert a.delay_s == pytest.approx(-delay, abs=0.3e-9)


def test_safe_name():
    assert safe_name(" fiber A/1? ") == "fiber_A1"
    assert safe_name("") == "measurement"


def _run(seq, req, prompts):
    out = []
    seq.cb.run_finished = lambda kind, path, meta: out.append((kind, path))

    def cable(run, total):
        prompts.append(run)
        seq.confirm_cable(True)

    seq.cb.request_cable = cable
    seq.run(req)
    return out


def test_sequence_series_and_abort(tmp_path, settings):
    folder = MeasurementFolder(tmp_path)
    moku = SimMokuDevice()
    seq = AcquisitionSequence(moku, SimWavegen(), settings, folder, SequenceCallbacks())
    prompts = []
    out = _run(seq, RunRequest(kind=CALIBRATION), prompts)
    assert out[0][0] == CALIBRATION and folder.latest_calibration() is not None
    out = _run(seq, RunRequest(kind=MEASUREMENT, name="fut", repeats=2), prompts)
    assert [p.split("_")[-1] for _, p in out] == ["r001.csv", "r002.csv"]
    # cable prompt only once: the phasemeter stayed deployed with unchanged settings
    assert prompts == [1]
    # after using the oscilloscope the phasemeter is re-deployed -> prompt again
    moku.oscilloscope()
    _run(seq, RunRequest(kind=MEASUREMENT, name="fut"), prompts)
    assert prompts == [1, 1]
    # a changed setting also requires the prompt
    settings.phasemeter.pll_bandwidth = "10kHz"
    _run(seq, RunRequest(kind=MEASUREMENT, name="fut"), prompts)
    assert prompts == [1, 1, 1]

    # abort during the sweep stops cleanly and leaves the generator off
    wg = SimWavegen()
    seq = AcquisitionSequence(SimMokuDevice(), wg, settings, folder, SequenceCallbacks())
    seq.cb.request_cable = lambda run, total: seq.confirm_cable(True)
    threading.Timer(1.5, seq.abort).start()
    with pytest.raises(Aborted):
        seq.run(RunRequest(kind=MEASUREMENT, name="x"))
    assert not wg.output_on
