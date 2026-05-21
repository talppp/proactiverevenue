"""
Structured logging via structlog. Every layer logs (layer, event, message_id,
**ctx) so an audit trail across the pipeline is grep-able.

Use:
    from app.logging_setup import get_logger
    log = get_logger("ingestion")
    log.info("normalized", message_id=mid, channel="sms")
"""
import logging
import sys

import structlog


def setup(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)),
        cache_logger_on_first_use=True,
    )


def get_logger(layer: str):
    return structlog.get_logger().bind(layer=layer)


setup()
