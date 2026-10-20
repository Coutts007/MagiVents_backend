# Load tests

k6 scripts and an isolated Docker stack (project `magiload`) laid out like `docker-compose.prod.yml`: PostgreSQL,
Redis, the API under Gunicorn, the mailer and nginx with `../../magivents_interface/nginx.conf`. The database lives
in memory and nothing is shared with the development stack. The stack is published on port 8100.

Cores are pinned so the parts do not compete: API on 0-3 (a 4-vCPU server), nginx and the mailer on 4, PostgreSQL and
Redis on 5, k6 on 6-7. Edit the `cpuset` lines for another machine.

## Run

```bash
cd loadtest
docker compose build api && docker compose up -d
docker compose exec -T api python manage.py shell < prep.py          # 3,000 test users with tokens; free events get unlimited places
docker compose cp api:/tmp/tokens.json ./tokens.json
docker compose exec -T -e TARGET=200 api python manage.py shell < grow.py   # optional: 200 events
docker compose exec -T db psql -U magi -d magi < seed_bookings.sql          # optional: 100,000 past bookings

./run.sh app.js app_1000 USERS=1000                 # results/app_1000.txt (summary) and .json
docker compose down                                  # removes everything, data included
```

## Scripts

| Script | What it simulates |
|---|---|
| `app.js` | The real frontend: each visit loads `/config/` and the whole event list once (attendees without their token, so it comes from the cache), signed-in people also load profile, saved events and tickets, then browse in the browser for 15-45 s. About 1 visit in 10 books (half free, half paid by simulated M-Pesa, polled every 2.5 s). 60% visitors, 40% signed in |
| `journeys.js` | The heavier model of the first test: every step fetches the list, search results and event pages, with 60% visitors, 30% members, 10% buyers |
| `spike.js` | Everyone books the same free event within 10 s |
| `login.js` | Sign-ins per second (password hashing is slow on purpose; rate limits apply per account and IP) |
| `rps.js`, `bench.js` | Maximum throughput of one endpoint, anonymous or signed in |

## Results (20 October 2026, 4 API cores, 200 events, 100,000 bookings)

`app.js`, 90 s ramp then 3 minutes at the given number of people:

| Simultaneous users | Requests/s | Errors | p95 overall | p95 event list | p95 signed-in pages | p95 booking |
|---|---|---|---|---|---|---|
| 2,500 | 129 | 0% | 29 ms | 14 ms | 19 ms | 95 ms |
| 5,000 | 260 | 0% | 83 ms | 38 ms | 69 ms | 318 ms |
| **10,000** | **516** | **0%** | **515 ms** | **3 ms** | **646 ms** | **899 ms** |
| 12,500 | 592 | 0% | 9.8 s | 4 ms | 12.2 s | 13.8 s |

The 2,500 and 5,000 runs were made before the cached sign-in user and the attendee list without a token; the 10,000
and 12,500 runs include every change. At 10,000 the API's 4 cores and the database's single core are fully used:
this is the most a server this size takes. Before the changes, the same 200-event, 100,000-booking database lagged
at 100 users (p95 5.9 s, `journeys.js`).
