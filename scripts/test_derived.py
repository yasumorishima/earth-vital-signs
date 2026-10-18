"""Check the derived columns on small hand-made inputs: the C3S pre-industrial offset, CPC's ENSO episode rule and
its table parser, year-to-date means and 7-day changes, and the AGGI forcing expressions. Each case states the
answer worked out by hand, so a wrong sign, phase, rounding or run length fails here."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)


# C3S offset: 0.88 + 0.05 sin(2pi(n-0.5)/365) + 0.07 cos(2pi(n-0.5)/365)
def by_hand(n):
    return 0.88 + 0.05 * math.sin(2 * math.pi * (n - 0.5) / 365) + 0.07 * math.cos(2 * math.pi * (n - 0.5) / 365)


d = pd.Series(pd.to_datetime(["2023-01-01", "2023-04-10", "2023-11-17", "2024-02-29", "2024-03-01", "2023-12-31"]))
o = build.c3s_preindustrial_offset(d).to_numpy()
want = [by_hand(1), by_hand(100), by_hand(321), (by_hand(59) + by_hand(60)) / 2, by_hand(60), by_hand(365)]
check(f"offset at n=1,100,321,Feb29,Mar1(leap),365 {np.round(o, 4).tolist()}", np.allclose(o, want, atol=1e-12))
check("offset Jan 1 is 0.950", abs(o[0] - 0.9504) < 1e-3)
check("Mar 1 in a leap year is day 60 of a 365-day year", abs(o[4] - by_hand(60)) < 1e-12)

# CPC episodes
s = pd.Series([0.47, 0.5, 0.6, 0.55, 0.52,            # 5 seasons, first rounds 0.47 -> 0.5: El Nino
               0.44, 0.0, -0.5, -0.6, -0.7, -0.6,      # 0.44 -> 0.4 breaks; 4 cold seasons: neutral
               0.1, -0.45, -0.9, -1.0, -0.8])          # -0.45 -> -0.5: open run of 4 at the end: undetermined
e = build.enso_episodes(s).tolist()
check(f"episodes {e}", e == ["el_nino"] * 5 + ["neutral"] * 6 + ["neutral"] + [""] * 4)
s2 = pd.Series([-0.5, -0.6, -0.7, -0.8, -0.9, np.nan])
check("closed La Nina run, trailing NaN empty", build.enso_episodes(s2).tolist() == ["la_nina"] * 5 + [""])
s3 = pd.Series([0.6, 0.7, 0.8, 0.9, np.nan])
check("run of 4 ending at the last value is undetermined", build.enso_episodes(s3).tolist() == [""] * 4 + [""])
check("cpc_round half away from zero", build.cpc_round(np.array([0.45, -0.45, 0.449, 0.25, -0.15])).tolist()
      == [0.5, -0.5, 0.4, 0.3, -0.2])

# CPC table parser, both page styles
roni = ('<table><tbody><tr><th>Year</th><th>DJF</th></tr><tr><th scope="row"><p>2024</p></th>'
        '<td><span class="roni-warm" aria-x="y">1.5</span></td><td><span class="roni-normal">0.2</span></td></tr>'
        '<tr id="latest-data"><th scope="row"><p>2025</p></th><td><span class="roni-cold">-0.6</span></td>'
        '</tbody></table>')
oni = ('<table><tr><td><strong>Year</strong></td><td>DJF</td></tr><tr><td width="7%"><font><strong>2024</strong>'
       '</font></td><td><span style="color:red"><font>1.8</font></span></td><td><span style="color:black">'
       '<font>0.1</font></span></td><td><span style="color:blue"><font>-0.5</font></span></td></tr></table>')
for name, page, exp in [("roni", roni, [("2024-01", 1.5, "warm"), ("2024-02", 0.2, "normal"), ("2025-01", -0.6, "cold")]),
                        ("oni", oni, [("2024-01", 1.8, "warm"), ("2024-02", 0.1, "normal"), ("2024-03", -0.5, "cold")])]:
    build.get = lambda url, page=page: page.encode()
    t = build.cpc_table("x")
    got = [(r.date.strftime("%Y-%m"), r.cpc_value, r.cpc_class) for r in t.itertuples()]
    check(f"cpc_table {name}: {got}", got == exp)

# the episode check against CPC's page when the file and the page are updated at different times
def episode_case(n_file, n_page, page_colour, flip_at=None):
    vals = [0.0] * 950 + [0.6] * 5
    dates = pd.date_range("1900-01-01", periods=len(vals), freq="MS")
    f = pd.DataFrame({"date": dates[:n_file], "roni_anom": vals[:n_file]})
    f["roni_episode"] = build.enso_episodes(f.roni_anom)
    cls = [page_colour(i) for i in range(n_page)]
    if flip_at is not None:
        cls[flip_at] = "warm"
    page = pd.DataFrame({"date": dates[:n_page], "cpc_value": build.cpc_round(np.array(vals[:n_page])),
                         "cpc_class": cls})
    build.cpc_table = lambda url: page
    build.FAIL.clear()
    build.cpc_episode_check(f, "roni_episode", "x")
    return list(build.FAIL)


real_cpc_table = build.cpc_table
closed = lambda i: "warm" if i >= 950 else "normal"   # a page that has seen the whole 5-season run
check("file completes a run the page has not reached: no failure",
      episode_case(955, 954, lambda i: "normal") == [])
check("page has seen the 5th season the file lacks: no failure", episode_case(954, 955, closed) == [])
check("page and file both complete: no failure", episode_case(955, 955, closed) == [])
check("a neutral season coloured warm by the page fails", any("unexplained" in m for m in
                                                              episode_case(955, 955, closed, flip_at=300)))
build.cpc_table = real_cpc_table

# year-to-date mean and 7-day change
df = pd.DataFrame({"date": pd.date_range("2000-12-30", "2001-01-10"), "v": np.arange(12, dtype=float)})
y = build.ytd_mean(df, "v")
check("ytd empty for a year that does not start on 1 Jan", y.iloc[:2].isna().all())
check("ytd restarts on 1 Jan (2,2.5,3,...)", np.allclose(y.iloc[2:6], [2, 2.5, 3, 3.5]))
c = build.change_over(df, "v", 7)
check("7-day change empty for the first 7 days, then 7", c.iloc[:7].isna().all() and (c.iloc[7:] == 7).all())
gap = df.drop(index=3)
check("7-day change empty when the earlier day is missing",
      np.isnan(build.change_over(gap.reset_index(drop=True), "v", 7).iloc[9]))

# AGGI expressions: zero at the 1750 abundances; CO2 doubling about 3.9 W m-2 effective (ERF per Smith et al. 2021)
z = build.erf_gases(np.array([278.3]), np.array([729.2]), np.array([270.1]))
check(f"forcing zero at 1750 levels {[float(v[0]) for v in z]}", all(abs(v[0]) < 1e-12 for v in z))
dbl = build.erf_gases(np.array([556.6]), np.array([729.2]), np.array([270.1]))[0][0]
check(f"CO2 doubling ERF {dbl:.2f} W m-2 within 3.7-4.1", 3.7 <= dbl <= 4.1)

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
