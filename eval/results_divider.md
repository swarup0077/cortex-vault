# Divider rerun

Run 2026-10-19T17:39:30 at commit 1cd19ad, against the final run 2026-10-19T17:04:07 at commit 6d4c860. Only the divider's numbers are reported; the ranking is checked against the final run's.

| half | queries | R@1, R@5, R@10, MRR@10 as in the final run | every query's rank as in the final run | above the divider (median) | every correct file above it |
|---|---|---|---|---|---|
| Tuning half | 22 | yes | yes | 40.0 → 19.0 | 22 → 20 of 22 (private 19, 14 of 15; public 28, 6 of 7) |
| Held-out half | 22 | yes | yes | 37.0 → 19.0 | 21 → 18 of 22 (private 18.5, 14 of 16; public 20.0, 4 of 6) |
| Both halves | 44 | yes | yes | 39.5 → 19.0 | 43 → 38 of 44 (private 19, 28 of 31; public 21, 10 of 13) |
