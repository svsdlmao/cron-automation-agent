# Evaluation: rules

Run 2026-10-28T22:33:00

- Fully correct: **19/20 (95%)**
- Schedule correct on scheduling prompts: 15/15
- Median planning time: 0.0 s (95th percentile 0.001 s)

| Category | Correct |
|---|---|
| ask | 2/2 |
| basic | 1/2 |
| daily | 1/1 |
| edge | 2/2 |
| intent | 2/2 |
| interval | 3/3 |
| monthly | 2/2 |
| one-off | 1/1 |
| weekly | 3/3 |
| window | 1/1 |
| yearly | 1/1 |

## Misses

| id | prompt | why |
|---|---|---|
| 115 | generate the weekly report every Monday at 7:45am | command target 'generate report' |
