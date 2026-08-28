# Slow crawl on a 4k-page site — what am I doing wrong?

**asked by mira**

I am crawling an internal documentation site, about 4,000 pages. Sequential
fetching takes roughly 40 minutes. I set `max_concurrency=32` and it got
*slower*. Is that expected?

**answer by tvk (accepted)**

Almost certainly yes. Three things happen at once at that level:

- The origin starts queueing you, so latency per request climbs.
- Your own DNS resolver becomes the bottleneck if the pool is per-host.
- Markdown conversion is CPU work and competes with the event loop.

Try 8 first and measure. Concurrency past the point where the server queues
you buys nothing and makes the tail latency much worse.

**comment by mira**

8 gives 6 minutes. 32 gave 11. Thank you — I was treating concurrency as a
dial that only goes up.
