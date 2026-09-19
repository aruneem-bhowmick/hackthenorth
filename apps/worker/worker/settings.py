import truststore

# See apps/api/app/main.py for why this runs first.
truststore.inject_into_ssl()

import sentry_sdk
from arq.connections import RedisSettings

from worker.config import get_settings
from worker.tasks import process_citation, process_job

_settings = get_settings()

if _settings.sentry_dsn_worker:
    sentry_sdk.init(
        dsn=_settings.sentry_dsn_worker,
        environment=_settings.sentry_environment,
        traces_sample_rate=1.0,
        send_default_pii=False,
    )


class WorkerSettings:
    functions = [process_job, process_citation]
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
