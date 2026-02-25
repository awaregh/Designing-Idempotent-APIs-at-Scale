"""
Transactional Outbox Processor.

Polls outbox_events WHERE published = false, publishes each event
to RabbitMQ, then marks it published = true.

Uses a PostgreSQL advisory lock (pg_try_advisory_lock) to prevent
concurrent processors from publishing the same event twice.
"""
from __future__ import annotations

import asyncio
import json
import os

import aio_pika
import structlog
from sqlalchemy import select, text, update

from services.shared.database import AsyncSessionFactory
from services.shared.models import OutboxEvent

logger = structlog.get_logger(__name__)

RABBITMQ_URL: str = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
ADVISORY_LOCK_ID = 999_001  # arbitrary stable integer for the outbox processor


async def process_batch(limit: int = 100) -> int:
    """
    Fetch and publish up to `limit` unpublished outbox events.

    Returns the number of events successfully published.
    """
    published_count = 0

    async with AsyncSessionFactory() as session:
        # Acquire advisory lock — skip batch if another instance holds it
        lock_result = await session.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": ADVISORY_LOCK_ID},
        )
        acquired = lock_result.scalar()
        if not acquired:
            logger.debug("outbox_advisory_lock_not_acquired")
            return 0

        try:
            # Fetch unpublished events
            result = await session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.published.is_(False))
                .order_by(OutboxEvent.created_at)
                .limit(limit)
            )
            events = result.scalars().all()

            if not events:
                return 0

            # Publish to RabbitMQ
            connection = await aio_pika.connect_robust(RABBITMQ_URL)
            async with connection:
                channel = await connection.channel()

                for event in events:
                    try:
                        exchange = channel.default_exchange
                        message = aio_pika.Message(
                            body=json.dumps(event.payload).encode(),
                            message_id=str(event.id),
                            content_type="application/json",
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        )
                        await exchange.publish(
                            message, routing_key=event.event_type
                        )

                        # Mark published within same transaction
                        await session.execute(
                            update(OutboxEvent)
                            .where(OutboxEvent.id == event.id)
                            .values(published=True)
                        )
                        published_count += 1
                        logger.info(
                            "outbox_event_published",
                            event_id=str(event.id),
                            event_type=event.event_type,
                        )

                    except Exception as exc:
                        logger.error(
                            "outbox_publish_failed",
                            event_id=str(event.id),
                            error=str(exc),
                        )

            await session.commit()

        finally:
            # Always release advisory lock
            await session.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": ADVISORY_LOCK_ID},
            )

    return published_count


async def start_processor(interval: float = 5.0) -> None:
    """Run the outbox processor loop indefinitely."""
    logger.info("outbox_processor_started", interval=interval)
    while True:
        try:
            count = await process_batch()
            if count:
                logger.info("outbox_batch_processed", count=count)
        except asyncio.CancelledError:
            logger.info("outbox_processor_cancelled")
            break
        except Exception as exc:
            logger.error("outbox_processor_error", error=str(exc))

        await asyncio.sleep(interval)
