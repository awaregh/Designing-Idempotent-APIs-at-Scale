"""
RabbitMQ consumer with consumer-side deduplication.

For each incoming message:
1. Extract message_id from AMQP message properties.
2. SELECT * FROM dedup_records WHERE message_id = ?
3. If record exists → log "Duplicate message, skipping" and ack.
4. If not exists  → process payment, INSERT DedupRecord, ack.

This pattern tolerates at-least-once delivery from the broker.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime

import aio_pika
import structlog
from aio_pika import IncomingMessage
from sqlalchemy import select

from services.shared.database import AsyncSessionFactory
from services.shared.models import DedupRecord, Payment

logger = structlog.get_logger(__name__)

RABBITMQ_URL: str = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
QUEUE_NAME = "payments"


async def process_message(message: IncomingMessage) -> None:
    """Deduplicate and process a single payment message."""
    async with message.process():
        message_id = message.message_id or str(uuid.uuid4())

        try:
            body = json.loads(message.body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("consumer_decode_error", message_id=message_id, error=str(exc))
            return

        async with AsyncSessionFactory() as session:
            # Deduplication check
            result = await session.execute(
                select(DedupRecord).where(DedupRecord.message_id == message_id)
            )
            existing = result.scalar_one_or_none()

            if existing is not None:
                logger.info(
                    "duplicate_message_skipped",
                    message_id=message_id,
                    strategy="dedup_queue",
                )
                return

            # Process payment
            try:
                payment = Payment(
                    id=uuid.uuid4(),
                    amount=body.get("amount", "0"),
                    currency=body.get("currency", "USD"),
                    customer_id=body.get("customer_id", "unknown"),
                    description=body.get("description"),
                    status="completed",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(payment)
                await session.flush()

                # Record dedup
                dedup = DedupRecord(
                    message_id=message_id,
                    processed_at=datetime.utcnow(),
                    result={
                        "payment_id": str(payment.id),
                        "status": "completed",
                        "amount": str(body.get("amount")),
                        "currency": body.get("currency"),
                    },
                )
                session.add(dedup)
                await session.commit()

                logger.info(
                    "message_processed",
                    message_id=message_id,
                    payment_id=str(payment.id),
                )

            except Exception as exc:
                await session.rollback()
                logger.error(
                    "consumer_processing_error",
                    message_id=message_id,
                    error=str(exc),
                )
                raise  # Let aio-pika nack/requeue


async def start_consumer() -> None:
    """Start the RabbitMQ consumer loop with reconnection."""
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL)
            logger.info("consumer_connected", url=RABBITMQ_URL)

            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=10)
                queue = await channel.declare_queue(QUEUE_NAME, durable=True)
                await queue.consume(process_message)
                logger.info("consumer_started", queue=QUEUE_NAME)
                # Keep running until connection drops
                await asyncio.Future()

        except asyncio.CancelledError:
            logger.info("consumer_cancelled")
            break
        except Exception as exc:
            logger.error("consumer_connection_error", error=str(exc))
            await asyncio.sleep(5)
