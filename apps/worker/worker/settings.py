import truststore

# See apps/api/app/main.py for why this runs first.
truststore.inject_into_ssl()

import sentry_sdk
from arq.connections import RedisSettings

from worker.config import get_settings
from worker.tasks import (
    process_citation,
    process_investigation,
    process_job,
    process_proposition,
    process_quote,
    process_source,
    shutdown,
    startup,
)

_settings = get_settings()

if _settings.sentry_dsn_worker:
    sentry_sdk.init(
        dsn=_settings.sentry_dsn_worker,
        environment=_settings.sentry_environment,
        traces_sample_rate=1.0,
        send_default_pii=False,
    )


class WorkerSettings:
    functions = [
        process_job,
        process_citation,
        process_source,
        process_quote,
        process_proposition,
        process_investigation,
    ]
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown
    # Resolution is database-safe at this concurrency. Source downloads have
    # their own four-request semaphore and never hold a session while waiting.
    max_jobs = 10
    # Keep the default five-minute per-citation timeout.  The P1 latency target
    # is monitored at the job level; turning it into a 90-second hard kill
    # causes slow upstream lookups to retry and congest the queue.
    max_tries = 2
