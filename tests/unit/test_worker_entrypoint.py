#!/usr/bin/env python3
"""
Module:   Test Worker Entrypoint
Purpose:  Pin that the `python -m forge.events` entrypoint (the one
          overridden as the ECS worker container CMD in
          infra/govcloud/ecs.py) is importable, wires graceful
          shutdown, and delegates to `run_forever`.  Without this
          test a silent breakage could ship the worker service
          pointing at a module that crashes on import; ECS would
          restart it in a loop.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import signal
from typing import Any
from unittest.mock import AsyncMock, patch


def test_module_importable_as_main() -> None:
    """`python -m forge.events` resolves to the entrypoint module.

    Importing is enough to prove the module loads.  A regression
    (syntax error, missing dep) would fail here before CI ever
    reached the ECS deployment.
    """
    mod = importlib.import_module("forge.events.__main__")
    assert hasattr(mod, "main")
    assert callable(mod.main)


def test_signal_handlers_registered_for_sigterm_and_sigint() -> None:
    """Graceful shutdown depends on SIGTERM (ECS) + SIGINT (dev).

    We patch `run_forever` to return immediately and capture every
    `add_signal_handler` call on the loop.  Both SIGTERM and SIGINT
    must be wired or an ECS rolling deploy will hang until the
    stop-timeout sends SIGKILL.
    """
    from forge.events import __main__ as entry

    captured_signals: list[int] = []

    class _StubLoop:
        def add_signal_handler(
            self, sig: int, callback: Any, *args: Any,
        ) -> None:
            captured_signals.append(int(sig))

    async def fake_run_forever(*_args: Any, **_kwargs: Any) -> None:
        # Returns after one tick so the entrypoint's "worker
        # finished on its own" branch executes and the signal
        # handlers have been registered by that point.
        await asyncio.sleep(0)

    with (
        patch.object(
            asyncio, "get_running_loop",
            return_value=_StubLoop(),
        ),
        patch.object(
            entry, "run_forever",
            new=AsyncMock(side_effect=fake_run_forever),
        ),
    ):
        asyncio.run(entry._main())

    assert signal.SIGTERM in captured_signals, (
        "SIGTERM handler not registered — ECS rolling deploys will hang"
    )
    assert signal.SIGINT in captured_signals, (
        "SIGINT handler not registered — Ctrl-C won't drain cleanly in dev"
    )


def test_stop_event_cancels_worker_task() -> None:
    """When the stop-event fires, the entrypoint cancels the
    run_forever task and exits 0 (graceful).

    We wrap the real `add_signal_handler` so the signal handler
    registration is observable but the actual loop is untouched.
    A fast `run_forever` that awaits a long sleep simulates an
    in-flight worker iteration; after the handlers register, we
    fire the captured SIGTERM callback ourselves via call_later.
    """
    from forge.events import __main__ as entry

    cancelled: list[bool] = []

    async def fake_run_forever(*_args: Any, **_kwargs: Any) -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def _driver() -> int:
        loop = asyncio.get_running_loop()
        original_add = loop.add_signal_handler
        captured: dict[str, Any] = {}

        def _wrapped_add(
            sig: int, callback: Any, *args: Any,
        ) -> None:
            if sig == signal.SIGTERM:
                captured["cb"] = callback
                captured["args"] = args
                # Schedule the SIGTERM callback to fire shortly
                # after the entrypoint enters asyncio.wait.
                loop.call_later(
                    0.05,
                    lambda: callback(*args),
                )
                return
            # Real SIGINT registration fails on Windows; swallow
            # NotImplementedError to keep the test portable.
            with contextlib.suppress(NotImplementedError):
                original_add(sig, callback, *args)

        # Monkeypatch the loop's signal-handler method so we can
        # observe and drive the SIGTERM callback.  mypy flags this
        # as a type-assignment mismatch against the stdlib stub; the
        # narrower stub is fine at runtime.
        loop.add_signal_handler = _wrapped_add  # type: ignore[assignment,method-assign]
        return await entry._main()

    with patch.object(
        entry, "run_forever",
        new=AsyncMock(side_effect=fake_run_forever),
    ):
        exit_code = asyncio.run(_driver())

    assert exit_code == 0, (
        "graceful shutdown must exit 0 so ECS does not trigger "
        "UnhealthyTaskCount alarm"
    )
    assert cancelled == [True], (
        "worker task was not cancelled when stop-event fired"
    )


def test_run_forever_crash_surfaces_as_nonzero_exit() -> None:
    """If run_forever raises (e.g. config init crash), the
    entrypoint must exit with a non-zero code so ECS restarts
    the task and the deployment circuit breaker can eventually
    roll back."""
    from forge.events import __main__ as entry

    class _BoomError(RuntimeError):
        pass

    async def fake_crash(*_a: Any, **_kw: Any) -> None:
        raise _BoomError("simulated startup failure")

    class _NoopLoop:
        def add_signal_handler(self, *_a: Any, **_kw: Any) -> None:
            return None

    with (
        patch.object(
            asyncio, "get_running_loop", return_value=_NoopLoop(),
        ),
        patch.object(
            entry, "run_forever",
            new=AsyncMock(side_effect=fake_crash),
        ),
    ):
        exit_code = asyncio.run(entry._main())

    assert exit_code == 1
