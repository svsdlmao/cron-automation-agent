# Evaluation: ollama (llama3.2)

Run 2026-10-22T22:52:46

- Fully correct: **27/59 (46%)**
- Schedule correct on scheduling prompts: 16/41
- Median planning time: 1.111 s (95th percentile 2.195 s)
- System result after recovery (model or rules): 38/59
- Invalid first answers: 14 — recovered by retry 3, by falling back to rules 11; correct afterwards 12/14
- Kinds: invalid cron syntax 9, missing argument 5

| Category | Correct |
|---|---|
| ask | 3/7 |
| basic | 2/4 |
| daily | 2/7 |
| edge | 0/5 |
| intent | 8/9 |
| interval | 3/8 |
| monthly | 4/4 |
| one-off | 1/2 |
| weekly | 2/7 |
| window | 0/3 |
| yearly | 2/3 |

## Misses

| id | prompt | why |
|---|---|---|
| 1 | run ~/scripts/db_backup.sh every Friday at midnight | schedule ['0 0 5 * *'] != ['0 0 * * 5'] |
| 2 | Run my database backup script every Friday at midnight. | command target 'find_script' |
| 3 | every 15 minutes run ~/bin/health_check.sh | model failed; fell back to rules |
| 6 | every half hour run ~/bin/sync.sh | schedule ['30 * * * *'] != ['*/30 * * * *'] |
| 7 | every hour run ~/bin/rotate.sh | model failed; fell back to rules |
| 9 | every 2 hours run ~/bin/fetch.sh | model failed; fell back to rules |
| 10 | every 6 hours run ~/bin/fetch.sh | schedule ['0 * * * *'] != ['0 */6 * * *'] |
| 11 | every day at 9am run ~/scripts/report.py | schedule ['9 * * * *'] != ['0 9 * * *'] |
| 12 | daily at 14:30 run ~/scripts/report.py | model failed; fell back to rules |
| 14 | every night at 11pm run ~/scripts/cleanup_tmp.sh | model failed; fell back to rules |
| 16 | every day at 9am and 5pm run ~/bin/check.sh | schedule ['9 * * * *'] != ['0 9,17 * * *'] |
| 17 | every day at 8:30am and 6pm run ~/bin/check.sh | model failed; fell back to rules |
| 19 | monday to friday at 7am run ~/bin/wake.sh | got ask |
| 20 | every weekend at 10am run ~/bin/garden.sh | model failed; fell back to rules |
| 21 | every Monday at 8am run ~/bin/plan.sh | model failed; fell back to rules |
| 22 | every Monday and Thursday at 6pm run ~/bin/gym.sh | schedule ['0 18 * * 1,2'] != ['0 18 * * 1,4'] |
| 23 | on Tuesdays and Fridays at 7:15 run ~/bin/bins.sh | schedule ['15 15 * * 2,5'] != ['15 7 * * 2,5'] |
| 25 | every 15 minutes during working hours on weekdays run ~/bin/health_check.sh | got ask |
| 26 | every 10 minutes between 8am and 6pm run ~/bin/poll.sh | got ask |
| 27 | every 2 hours between 9am and 5pm on weekdays run ~/bin/sync.sh | got ask |
| 34 | every 3 months on the 1st at 8am run ~/bin/quarterly.sh | schedule ['0 8 1 * 3'] != ['0 8 1 */3 *'] |
| 36 | back up my database on the first Monday of every month at 9am | got ask |
| 37 | on the last day of the month at 23:00 run ~/scripts/close_books.sh | model failed; fell back to rules |
| 38 | on the last Friday of the month at 5pm run ~/scripts/payroll.sh | schedule ['0 17 30 * 5'] != ['0 17 * * 5']; guard None != last-weekday |
| 39 | every 90 minutes run ~/bin/sync.sh | schedule ['0,30 * * * *'] != ['0 0-21/3 * * *', '30 1-22/3 * * *'] |
| 41 | every 45 minutes run ~/bin/poll.sh | schedule ['45 * * * *'] != ['*/45 * * * *'] |
| 42 | every day run ~/bin/x.sh | got tool add_job |
| 43 | every Friday run ~/bin/x.sh | got tool add_job |
| 46 | every 30 seconds run ~/bin/x.sh | model failed; fell back to rules |
| 47 | every other week on monday at 9am run ~/bin/x.sh | model failed; fell back to rules |
| 49 | in 10 minutes run ~/bin/x.sh | got tool add_job |
| 57 | undo that | got tool remove_job |
