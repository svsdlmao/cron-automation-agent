# Evaluation: rules

Run 2026-10-22T16:12:13

- Fully correct: **18/20 (90%)**
- Schedule correct on scheduling prompts: 14/15
- Median planning time: 0.0 s

| Category | Correct |
|---|---|
| ask | 2/2 |
| basic | 0/2 |
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
| 101 | Please run /srv/app/export.sh each Saturday at 4 in the afternoon | schedule ['0 4 * * 6'] != ['0 16 * * 6'] |
| 115 | generate the weekly report every Monday at 7:45am | command target 'generate report' |
