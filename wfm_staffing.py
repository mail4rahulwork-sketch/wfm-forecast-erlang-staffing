"""
WFM Forecast -> Erlang C Staffing Plan
--------------------------------------
End-to-end workforce-management pipeline on SIMULATED contact-centre data:

1. Generate 12 weeks of 30-minute interval call volumes (weekly + intraday
   seasonality, a Monday peak and random noise).
2. Forecast the next week with three methods and compare accuracy (MAPE / WAPE):
     - Seasonal naive (same interval last week)
     - 4-week average of the same weekday-interval
     - Weighted 4-week average (40/30/20/10)
3. Convert the best forecast into required agents per interval with Erlang C
   for a target service level of 80% of calls answered in 20 seconds.
4. Apply shrinkage to get the rostered headcount.
5. Compare interval-based staffing with a flat "peak-level all day" plan.

All data is synthetic. No company data is used.
Author: Rahul
"""
import math
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).parent / "outputs"
OUT.mkdir(exist_ok=True)
RNG = np.random.default_rng(42)

INTERVAL_MIN = 30
AHT_SEC = 300            # average handle time
TARGET_SL = 0.80         # 80% ...
TARGET_T = 20            # ... answered within 20 seconds
SHRINKAGE = 0.30         # breaks, training, leave, etc.
OPEN_H, CLOSE_H = 8, 22  # 08:00 - 22:00


# ---------------------------------------------------------------- 1. data
def simulate_volumes(weeks: int = 13) -> pd.DataFrame:
    start = pd.Timestamp("2026-08-03 00:00")  # a Monday
    idx = pd.date_range(start, periods=weeks * 7 * 48, freq=f"{INTERVAL_MIN}min")
    df = pd.DataFrame({"ts": idx})
    df = df[(df.ts.dt.hour >= OPEN_H) & (df.ts.dt.hour < CLOSE_H)].copy()
    h = df.ts.dt.hour + df.ts.dt.minute / 60
    # two intraday peaks: late morning and early evening
    intraday = 0.55 + 0.9 * np.exp(-((h - 11.5) ** 2) / 4) + 0.6 * np.exp(-((h - 18.5) ** 2) / 3)
    dow = df.ts.dt.dayofweek.map({0: 1.30, 1: 1.10, 2: 1.00, 3: 0.98, 4: 1.05, 5: 0.75, 6: 0.60})
    trend = 1 + 0.004 * ((df.ts - start).dt.days / 7)  # slow weekly growth
    base = 60 * intraday * dow * trend
    df["calls"] = RNG.poisson(base * RNG.normal(1, 0.06, len(df)).clip(0.7, 1.3))
    df["week"] = (df.ts - start).dt.days // 7
    df["dow"] = df.ts.dt.dayofweek
    df["slot"] = df.ts.dt.strftime("%H:%M")
    return df.reset_index(drop=True)


# ---------------------------------------------------------- 2. forecasting
def forecasts(df: pd.DataFrame, target_week: int) -> pd.DataFrame:
    hist = df[df.week < target_week]
    actual = df[df.week == target_week][["ts", "dow", "slot", "calls"]].copy()
    piv = hist.pivot_table(index=["dow", "slot"], columns="week", values="calls")
    last4 = piv[[target_week - k for k in (1, 2, 3, 4)]]
    f = pd.DataFrame(index=piv.index)
    f["seasonal_naive"] = last4[target_week - 1]
    f["avg_4wk"] = last4.mean(axis=1)
    f["weighted_4wk"] = (last4 * np.array([0.4, 0.3, 0.2, 0.1])).sum(axis=1)
    return actual.merge(f.reset_index(), on=["dow", "slot"])


def accuracy(fc: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for m in ["seasonal_naive", "avg_4wk", "weighted_4wk"]:
        err = (fc[m] - fc.calls).abs()
        rows.append({
            "method": m,
            "MAPE_%": round(100 * (err / fc.calls.clip(lower=1)).mean(), 1),
            "WAPE_%": round(100 * err.sum() / fc.calls.sum(), 1),
            "bias_%": round(100 * (fc[m].sum() - fc.calls.sum()) / fc.calls.sum(), 1),
        })
    return pd.DataFrame(rows).sort_values("WAPE_%")


# ------------------------------------------------------------- 3. Erlang C
def erlang_c(traffic: float, agents: int) -> float:
    """Probability a call has to wait (Erlang C)."""
    if agents <= traffic:
        return 1.0
    inv_b = 1.0
    for k in range(1, agents + 1):  # stable recursive Erlang B
        inv_b = 1 + inv_b * k / traffic
    b = 1 / inv_b
    return b / (1 - (traffic / agents) * (1 - b))


def service_level(calls: float, aht: float, agents: int, t: float) -> float:
    traffic = calls * aht / (INTERVAL_MIN * 60)
    if agents <= traffic:
        return 0.0
    pw = erlang_c(traffic, agents)
    return 1 - pw * math.exp(-(agents - traffic) * t / aht)


def required_agents(calls: float, aht: float = AHT_SEC, sl: float = TARGET_SL, t: float = TARGET_T) -> int:
    if calls <= 0:
        return 0
    traffic = calls * aht / (INTERVAL_MIN * 60)
    n = max(1, math.ceil(traffic))
    while service_level(calls, aht, n, t) < sl:
        n += 1
    return n


# ------------------------------------------------------------------ main
def main():
    df = simulate_volumes()
    target = df.week.max()
    fc = forecasts(df, target)
    acc = accuracy(fc)
    best = acc.iloc[0].method
    acc.to_csv(OUT / "forecast_accuracy.csv", index=False)

    fc["forecast"] = fc[best].round()
    fc["agents_needed"] = fc.forecast.apply(required_agents)
    fc["rostered_hc"] = np.ceil(fc.agents_needed / (1 - SHRINKAGE)).astype(int)
    fc["sl_achieved_on_actuals"] = [
        service_level(c, AHT_SEC, a, TARGET_T) for c, a in zip(fc.calls, fc.agents_needed)
    ]
    fc.to_csv(OUT / "staffing_plan.csv", index=False)

    # flat plan: staff every interval of each day at that day's peak requirement
    flat = fc.groupby(fc.ts.dt.date).agents_needed.transform("max")
    interval_hours = fc.agents_needed.sum() * INTERVAL_MIN / 60
    flat_hours = flat.sum() * INTERVAL_MIN / 60
    saving = 100 * (1 - interval_hours / flat_hours)

    # Erlang C non-linearity: occupancy at 10 vs 100 calls/interval
    ex = []
    for c in (10, 50, 100, 200):
        a = required_agents(c)
        occ = c * AHT_SEC / (INTERVAL_MIN * 60) / a
        ex.append({"calls_per_30min": c, "agents_needed": a, "occupancy_%": round(100 * occ, 1)})
    pd.DataFrame(ex).to_csv(OUT / "erlang_scale_effect.csv", index=False)

    intervals_met = (fc.sl_achieved_on_actuals >= TARGET_SL).mean() * 100

    # how much forecast buffer protects service level?
    buf_rows = []
    for buf in (0.0, 0.05, 0.10, 0.15):
        ag = (fc.forecast * (1 + buf)).round().apply(required_agents)
        met = np.mean([service_level(c, AHT_SEC, a, TARGET_T) >= TARGET_SL for c, a in zip(fc.calls, ag)]) * 100
        buf_rows.append({"forecast_buffer_%": int(buf * 100), "agent_hours": round(ag.sum() * INTERVAL_MIN / 60),
                         "intervals_meeting_80/20_%": round(met, 1)})
    buf_df = pd.DataFrame(buf_rows)
    buf_df.to_csv(OUT / "buffer_tradeoff.csv", index=False)

    # ---------------- charts
    mon = fc[fc.dow == 0]
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(mon.slot, mon.calls, label="Actual calls", lw=2, color="#1f3b73")
    ax.plot(mon.slot, mon.forecast, label=f"Forecast ({best})", lw=2, ls="--", color="#e07a1f")
    ax2 = ax.twinx()
    ax2.bar(mon.slot, mon.agents_needed, alpha=0.18, color="#2a9d8f", label="Agents needed (Erlang C)")
    ax.set_title("Monday: forecast vs actual and Erlang C staffing (80/20 SL)")
    ax.set_xticks(mon.slot[::4])
    ax.set_ylabel("Calls per 30 min")
    ax2.set_ylabel("Agents")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "monday_forecast_staffing.png", dpi=150)

    fig, ax = plt.subplots(figsize=(6, 3.8))
    ax.bar(acc.method, acc["WAPE_%"], color=["#2a9d8f", "#8ab17d", "#e9c46a"])
    ax.set_ylabel("WAPE %  (lower is better)")
    ax.set_title("Forecast accuracy on hold-out week")
    for i, v in enumerate(acc["WAPE_%"]):
        ax.text(i, v + 0.1, f"{v}%", ha="center")
    fig.tight_layout()
    fig.savefig(OUT / "forecast_accuracy.png", dpi=150)

    summary = f"""# Results (simulated data)

- Forecast methods tested on a hold-out week: best = **{best}**
{acc.to_markdown(index=False)}

- Interval-based Erlang C staffing needs **{interval_hours:,.0f} agent-hours** for the week,
  vs **{flat_hours:,.0f}** if each day is staffed flat at its peak -> **{saving:.1f}% fewer agent-hours**
  for the same 80/20 service-level target.
- Using the forecast-based plan, **{intervals_met:.0f}%** of intervals still meet 80/20 against *actual* volumes
  (the misses are where actual volume ran above forecast; this is what intraday management is for).
- Buffer trade-off (extra staffing on top of forecast vs intervals meeting 80/20 on actuals):
{buf_df.to_markdown(index=False)}
- Scale effect (Erlang C is non-linear):
{pd.DataFrame(ex).to_markdown(index=False)}
"""
    (OUT / "RESULTS.md").write_text(summary)
    print(summary)


if __name__ == "__main__":
    main()
