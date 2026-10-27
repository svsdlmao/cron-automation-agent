# Evaluation: ollama (llama3.2)

Run 2026-10-22T22:53:13

- Fully correct: **7/20 (35%)**
- Schedule correct on scheduling prompts: 3/15
- Median planning time: 0.971 s (95th percentile 2.06 s)
- System result after recovery (model or rules): 11/20
- Invalid first answers: 6 — recovered by retry 1, by falling back to rules 5; correct afterwards 4/6
- Kinds: invalid cron syntax 4, missing argument 2

| Category | Correct |
|---|---|
| ask | 1/2 |
| basic | 1/2 |
| daily | 1/1 |
| edge | 0/2 |
| intent | 2/2 |
| interval | 1/3 |
| monthly | 0/2 |
| one-off | 1/1 |
| weekly | 0/3 |
| window | 0/1 |
| yearly | 0/1 |

## Misses

| id | prompt | why |
|---|---|---|
| 102 | execute ~/bin/ping_api.sh every twenty minutes | schedule ['0,20 * * * *'] != ['*/20 * * * *'] |
| 104 | Every Wednesday at 10:30 pm, run ~/bin/digest.py | model failed; fell back to rules |
| 105 | run ~/bin/standup.sh at 9 on weekdays | schedule ['9 0 * * 5'] != ['0 9 * * 1-5'] |
| 106 | Saturdays and Sundays at 8:00, start ~/bin/backup_photos.sh | got ask |
| 107 | check ~/bin/queue.sh every 5 minutes from 7am to 7pm | got ask |
| 108 | on the 5th of each month at 10am run ~/scripts/bills.sh | model failed; fell back to rules |
| 109 | run ~/scripts/report.py on the 28th of every month at 18:00 | model failed; fell back to rules |
| 110 | every December 25th at 7am run ~/bin/greet.sh | schedule ['0 7 12 12 5'] != ['0 7 25 12 *'] |
| 112 | every 4 hours run `python3 /opt/sync.py --quiet` | schedule ['0 * * * *'] != ['0 */4 * * *'] |
| 113 | on the second Tuesday of every month at 6pm run ~/bin/patch.sh | model failed; fell back to rules |
| 114 | clean up temp files on the last day of every month at midnight | got ask |
| 115 | generate the weekly report every Monday at 7:45am | model failed; fell back to rules |
| 117 | back up the database regularly | got tool find_script |
