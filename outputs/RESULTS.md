# Results (simulated data)

- Forecast methods tested on a hold-out week: best = **avg_4wk**
| method         |   MAPE_% |   WAPE_% |   bias_% |
|:---------------|---------:|---------:|---------:|
| avg_4wk        |     14.1 |     13.1 |     -0.7 |
| weighted_4wk   |     14.1 |     13.2 |     -0.2 |
| seasonal_naive |     17.2 |     16.1 |      0.7 |

- Interval-based Erlang C staffing needs **1,258 agent-hours** for the week,
  vs **1,890** if each day is staffed flat at its peak -> **33.4% fewer agent-hours**
  for the same 80/20 service-level target.
- Using the forecast-based plan, **59%** of intervals still meet 80/20 against *actual* volumes
  (the misses are where actual volume ran above forecast; this is what intraday management is for).
- Buffer trade-off (extra staffing on top of forecast vs intervals meeting 80/20 on actuals):
|   forecast_buffer_% |   agent_hours |   intervals_meeting_80/20_% |
|--------------------:|--------------:|----------------------------:|
|                   0 |          1258 |                        58.7 |
|                   5 |          1310 |                        69.4 |
|                  10 |          1355 |                        76.5 |
|                  15 |          1410 |                        86.7 |
- Scale effect (Erlang C is non-linear):
|   calls_per_30min |   agents_needed |   occupancy_% |
|------------------:|----------------:|--------------:|
|                10 |               4 |          41.7 |
|                50 |              12 |          69.4 |
|               100 |              21 |          79.4 |
|               200 |              39 |          85.5 |
