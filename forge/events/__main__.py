#!/usr/bin/env python3
"""
Module:   Outbox Worker Entrypoint
Purpose:  Process entrypoint for the ECS worker task.  Wraps
          `forge.events.worker.run_forever` with structured logging,
          graceful-shutdown signal handling, and a deterministic exit
          path so the ECS deployment circuit breaker can observe
          task health correctly.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this module exists as a distinct entrypoint:
  The 6A-1 ECS scaffold provisioned a worker Fargate service that
  pulled the same image as the API.  Without an explicit CMD
  override the container would have booted `gunicorn forge.api.app:app`
  — an HTTP server, not an outbox poller.  `ecs.py` now overrides
  the worker container's command to `python -m forge.events`,
  which lands here.  Keeping the module wrapper small (and the
  loop itself in `worker.py`) means the loop stays unit-testable
  and the process-shape concerns (signal handling, logging
  initialization, exit code) live on their own.

Graceful shutdown:
  ECS sends SIGTERM during rolling deploys and scale-in.  We
  register a signal handler that sets an asyncio Event; the
  event loop watches that event and cancels the poll task so
  any in-flight batch commits or rolls back cleanly.  Exiting
  with code 0 tells ECS the shutdown was graceful and no
  `UnhealthyTaskCount` alarm is triggered.

  SIGINT is handled too for local dev (`Ctrl-C`).  SIGKILL is
  not catchable — if ECS hits the stop-timeout the container is
  force-killed; the outbox row stays locked until the
  SKIP-LOCKED polling window expires, at which point a sibling
  worker picks it up.  That's the whole point of using
  FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from forge.config import get_settings
from forge.events.worker import run_forever

logger = logging.getLogger("forge.events")


def _configure_logging() -> None:
    """Structured log format — timestamp, level, logger, message.

    Mirrors the API's Gunicorn access-log shape so ECS CloudWatch
    Logs panes render both services uniformly.  JSON logging could
    land in a later slice; for 6A-2 a deterministic text shape is
    sufficient for the operational smoke contract.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


async def _main() -> int:
    """Run the outbox worker until SIGTERM / SIGINT.

    Returns the process exit code.  0 on graceful shutdown; 1 if
    the run_forever task raises an unexpected exception that
    propagates out of the retry shield — that path is rare because
    `run_forever` already catches per-iteration failures, but a
    module-level crash (config load failure, DB pool init) should
    still surface to ECS as a non-zero exit.
    """
    _configure_logging()
    settings = get_settings()
    logger.info(
        "Starting outbox worker (env=%s, poll_interval=%ss)",
        settings.env, settings.outbox_poll_interval_seconds,
    )

    stop_event = asyncio.Event()

    def _request_stop(signum: int) -> None:
        logger.info(
            "Received signal %s; requesting graceful shutdown",
            signal.Signals(signum).name,
        )
        stop_event.set()

    loop = asyncio.get_running_loop()
    # POSIX signal handlers only; ECS Fargate runs Linux.  On
    # Windows the dev path relies on KeyboardInterrupt propagation,
    # which is fine because the worker is not a local-dev surface.
    for sig in (signal.SIGTERM, signal.SIGINT):
        # add_signal_handler is unsupported on Windows — suppress the
        # NotImplementedError so local dev still runs (SIGINT falls
        # back to the outer KeyboardInterrupt handler).  ECS Fargate
        # runs Linux so the production path always registers.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop, sig)

    worker_task = asyncio.create_task(
        run_forever(settings), name="outbox-run-forever",
    )
    stop_task = asyncio.create_task(
        stop_event.wait(), name="outbox-stop-signal",
    )

    done, pending = await asyncio.wait(
        {worker_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If the stop signal fired first, cancel the worker task and
    # wait up to 10 seconds for it to drain the in-flight batch.
    # 10s is well below ECS's default 30s stop-timeout so the
    # task usually finishes commit/rollback before the platform
    # sends SIGKILL.
    if stop_task in done:
        logger.info("Stop signal received; cancelling worker task")
        worker_task.cancel()
        try:
            await asyncio.wait_for(worker_task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning(
                "Worker task did not drain within 10s; exiting anyway",
            )
        return 0

    # Worker task finished on its own — run_forever is an infinite
    # loop that only exits on unhandled exception, so this path
    # means something unexpected happened.
    for task in pending:
        task.cancel()
    exc = worker_task.exception()
    if exc is not None:
        logger.exception("Outbox worker crashed", exc_info=exc)
        return 1
    logger.warning("Outbox worker returned without error; exiting")
    return 0


def main() -> None:
    """CLI entrypoint — `python -m forge.events`."""
    try:
        code = asyncio.run(_main())
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":  # pragma: no cover — process entrypoint
    main()
