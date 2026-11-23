# HttpClient — connection pooling

`HttpClient` keeps one connection pool per process. The pool is created
lazily on the first request and reused for every host afterwards, so a
batch of fetches against the same origin pays the TLS handshake once.

## Configuration

| Option | Default | Notes |
| --- | --- | --- |
| `pool_max_idle_per_host` | 8 | Raise this for crawl-heavy workloads. |
| `timeout` | 30s | Applies to the whole request, not per byte. |
| `user_agent` | library default | Set something identifying; some hosts refuse blank agents. |

## Retries

Idempotent requests are retried twice with exponential backoff. Non-idempotent
methods are never retried automatically — the caller decides, because only the
caller knows whether a duplicate POST is safe.

## Migrating from 1.x

The `build_client()` free function is gone. Construct the client directly and
hold it for the life of the process; constructing one per request defeats the
pool entirely and was the single most common performance complaint in 1.x.
