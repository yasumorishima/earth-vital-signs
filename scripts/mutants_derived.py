"""Plant defects in build.py's new derived columns; each must make its intended gate FAIL. Also run the unmodified
code once per target as a control, which must pass every gate. Run from the repo root on a machine with network."""
import importlib.util
import sys
from pathlib import Path

SRC = Path("scripts/build.py").read_text(encoding="utf-8")
MUT = {  # name: (target, original, replacement, substring of the gate that must fail)
    "flat_offset": ("air", "    o = f(n)\n", "    o = np.full(len(n), 0.88)\n", "C3S daily offset peaks"),
    "sin_sign": ("air", "0.88 + 0.05 * np.sin", "0.88 - 0.05 * np.sin", "C3S daily offset peaks"),
    "no_cos": ("air", " + 0.07 * np.cos(2 * np.pi * (n - 0.5) / 365)", "", "C3S daily offset peaks"),
    "ytd_across_years": ("air", "m = df.groupby(y)[col].cumsum()", "m = df[col].cumsum()", "year-to-date mean on 31 Dec"),
    "offset_dropped": ("air", "df[pi] = (a + off).round(4)", "df[pi] = (a + 0.80).round(4)", "C3S published"),
    # (Measured 2026-10-18: the "first day above 2.0 C" gate alone does not catch a flat 0.88 offset, since
    # 2023-11-17 is 1.155 + 0.88 = 2.035; it catches a dropped offset. The flat offset is caught by the shape gate.)
    "run_of_4":("enso", "if j - i + 1 >= 5:", "if j - i + 1 >= 4:", "unexplained"),
    # numpy rounds half to even, so it differs from CPC only next to a +-0.45 value: those seasons are exempt one by
    # one, and it is the cap on exempt seasons that stops the run (measured: 15 RONI seasons vs a cap of 6)
    "numpy_round": ("enso", "s = cpc_round(v)", "s = np.round(v, 1)", "seasons differ at a +-0.45 value"),
    "threshold_06": ("enso", "st = np.where(s >= 0.5, 1, np.where(s <= -0.5, -1, 0))",
                     "st = np.where(s >= 0.6, 1, np.where(s <= -0.6, -1, 0))", "unexplained"),
    "ch4_efficacy": ("erf", "a2, b2, d2, e2 = -8.96e-5, -1.25e-4, 0.045194, 0.86",
                     "a2, b2, d2, e2 = -8.96e-5, -1.25e-4, 0.045194, 1.0", "AGGI Table 2"),
    "n2o_preind": ("erf", "ERF_C0, ERF_M0, ERF_N0 = 278.3, 729.2, 270.1", "ERF_C0, ERF_M0, ERF_N0 = 278.3, 729.2, 275.0",
                   "AGGI Table 2"),
    "tsi_recalibrated": ("tsi", '"tsi_wm2": np.ma.filled(ds["TSI"][:].astype(float), np.nan).round(4)',
                         '"tsi_wm2": (np.ma.filled(ds["TSI"][:].astype(float), np.nan) + 0.05).round(4)',
                         "re-downloaded"),
}


def load(name, src):
    p = Path("scripts") / f"_mut_{name}.py"
    p.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"_mut_{name}", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    p.unlink()
    return m


def run(m, target):
    m.FAIL.clear()
    if target == "air":
        m.air()
    elif target == "enso":
        m.enso_monthly()
    elif target == "erf":
        m.erf_monthly(m.gases_monthly())
    elif target == "tsi":
        m.tsi_monthly()
    return list(m.FAIL)


ok = True
for t in ("air", "enso", "erf", "tsi"):
    fails = run(load(f"control_{t}", SRC), t)
    good = not fails
    ok &= good
    print(f"CONTROL {t}: {'PASS (no gate failed)' if good else 'FAILED ' + str(fails)}", flush=True)
for name, (t, a, b, want) in MUT.items():
    assert SRC.count(a) == 1, (name, SRC.count(a))
    try:
        fails = run(load(name, SRC.replace(a, b)), t)
    except Exception as e:  # a crash is not a gate
        fails = [f"CRASH {type(e).__name__}: {e}"]
    hit = any(want in f for f in fails)
    ok &= hit
    print(f"{'CAUGHT' if hit else 'MISSED'} {name}: intended gate '{want}'; failed: {[f[:110] for f in fails]}", flush=True)
print("ALL MUTANTS CAUGHT, CONTROLS CLEAN" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
