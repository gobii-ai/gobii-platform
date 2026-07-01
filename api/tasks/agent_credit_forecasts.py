import logging

from celery import shared_task
from django.conf import settings

from api.services.agent_credit_forecast_samples import seed_agent_credit_forecast_samples
from observability import traced

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True, name="api.tasks.refresh_agent_credit_forecast_samples")
def refresh_agent_credit_forecast_samples_task(self) -> dict:
    with traced("AGENT_CREDIT_FORECAST Refresh Samples") as span:
        result = seed_agent_credit_forecast_samples(
            limit=settings.AGENT_CREDIT_FORECAST_SAMPLE_REFRESH_LIMIT,
            generate_embeddings=settings.AGENT_CREDIT_FORECAST_SAMPLE_REFRESH_GENERATE_EMBEDDINGS,
            skip_existing_embeddings=settings.AGENT_CREDIT_FORECAST_SAMPLE_REFRESH_SKIP_EXISTING_EMBEDDINGS,
        )
        span.set_attribute("agent_credit_forecast_samples.upserted", result.upserted)
        span.set_attribute("agent_credit_forecast_samples.embedded", result.embedded)
        span.set_attribute("agent_credit_forecast_samples.skipped_embeddings", result.skipped_embeddings)
        logger.info(
            "Refreshed agent credit forecast samples: upserted=%s embedded=%s skipped_embeddings=%s",
            result.upserted,
            result.embedded,
            result.skipped_embeddings,
        )
        return {
            "status": "ok",
            "upserted": result.upserted,
            "embedded": result.embedded,
            "skipped_embeddings": result.skipped_embeddings,
        }
