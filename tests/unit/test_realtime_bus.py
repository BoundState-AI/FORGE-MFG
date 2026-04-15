#!/usr/bin/env python3
"""
Module:   Test Realtime Event Bus
Purpose:  Unit tests for forge.realtime — per-op channel bookkeeping,
          Last-Event-ID replay semantics, buffer-reset trigger, and the
          SSE stream helper control flow (ping / auth_expired).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The Operation Execution SSE channel is best-effort but its control
  surface (buffer replay, reset signal, ping cadence, auth_expired
  closure) is load-bearing for the reconnect contract in UX spec
  v1.5.1 §6.5.2.  A regression in this pure-Python helper propagates
  silently to the client — the SSE payload is the whole UX — so
  every control branch gets a small focused assertion.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from forge.realtime import (
    OperationEventBus,
    stream_operation_events,
)


class TestOperationEventBus:
    @pytest.mark.asyncio
    async def test_publish_assigns_monotonic_ids(self) -> None:
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        e1 = await bus.publish(op_id, "x", {"n": 1})
        e2 = await bus.publish(op_id, "x", {"n": 2})
        e3 = await bus.publish(op_id, "x", {"n": 3})
        assert e1.id == 1
        assert e2.id == 2
        assert e3.id == 3

    @pytest.mark.asyncio
    async def test_channels_are_isolated(self) -> None:
        bus = OperationEventBus()
        a, b = uuid.uuid4(), uuid.uuid4()
        await bus.publish(a, "x", {})
        await bus.publish(a, "x", {})
        e = await bus.publish(b, "x", {})
        assert e.id == 1  # b's counter is independent

    @pytest.mark.asyncio
    async def test_subscribe_without_hint_is_replay_ok_empty(self) -> None:
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        replay, queue, ok = await bus.subscribe(op_id, last_event_id=None)
        assert ok is True
        assert replay == []
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_replay_returns_events_after_hint(self) -> None:
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        await bus.publish(op_id, "x", {"n": 1})
        await bus.publish(op_id, "x", {"n": 2})
        await bus.publish(op_id, "x", {"n": 3})

        replay, _queue, ok = await bus.subscribe(op_id, last_event_id=1)
        assert ok is True
        assert [e.id for e in replay] == [2, 3]

    @pytest.mark.asyncio
    async def test_replay_signals_reset_when_buffer_rolled_past(self) -> None:
        """Stale Last-Event-ID → replay_ok=False → buffer_reset path."""
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        # Fill enough events that the ring has advanced far past id=1.
        for i in range(1, 400):
            await bus.publish(op_id, "x", {"n": i})

        replay, _queue, ok = await bus.subscribe(op_id, last_event_id=1)
        assert ok is False
        assert replay == []

    @pytest.mark.asyncio
    async def test_publish_fans_out_to_live_subscriber(self) -> None:
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        _replay, queue, _ok = await bus.subscribe(op_id)

        evt = await bus.publish(op_id, "hold_point_changed", {"n": 1})
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received == evt

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_queue(self) -> None:
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        _replay, queue, _ok = await bus.subscribe(op_id)
        await bus.unsubscribe(op_id, queue)

        # A subsequent publish must not enqueue onto the unsubscribed
        # queue — if it did, this would raise QueueFull-like surprises
        # on long-running tests.
        await bus.publish(op_id, "x", {})
        assert queue.empty()


class TestStreamOperationEvents:
    @pytest.mark.asyncio
    async def test_ping_has_monotonic_id_from_channel_counter(self) -> None:
        """Control frames (ping) carry a real monotonic id per spec §6.5.2."""
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        # Pre-publish a real event so the counter starts above zero;
        # the ping must land at a strictly greater id than the last
        # real event.
        await bus.publish(op_id, "hold_point_changed", {"n": 1})
        gen = stream_operation_events(
            op_id,
            last_event_id=1,  # skip replay of id=1
            bus=bus,
            token_expires_at=None,
            heartbeat_s=0.01,
        )
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert b"event: ping" in frame
        assert b"id: 2" in frame
        await gen.aclose()

    @pytest.mark.asyncio
    async def test_replay_then_live(self) -> None:
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        await bus.publish(op_id, "hold_point_changed", {"n": 1})
        await bus.publish(op_id, "hold_point_changed", {"n": 2})

        gen = stream_operation_events(
            op_id,
            last_event_id=0,  # replay everything
            bus=bus,
            token_expires_at=None,
            heartbeat_s=0.05,
        )
        frame1 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        frame2 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert b"id: 1" in frame1
        assert b"id: 2" in frame2
        await gen.aclose()

    @pytest.mark.asyncio
    async def test_buffer_reset_on_stale_hint_carries_monotonic_id(
        self,
    ) -> None:
        """buffer_reset lands at counter+1, preserving the id sequence."""
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        for i in range(1, 400):
            await bus.publish(op_id, "x", {"n": i})

        gen = stream_operation_events(
            op_id,
            last_event_id=1,
            bus=bus,
            token_expires_at=None,
            heartbeat_s=0.05,
        )
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert b"event: buffer_reset" in frame
        # 399 real publishes fired before subscribe; buffer_reset is
        # the 400th id-consuming emit on this channel.
        assert b"id: 400" in frame
        data = json.loads(frame.decode().split("data: ", 1)[1].strip())
        assert data == {"reason": "events_unavailable"}
        await gen.aclose()

    @pytest.mark.asyncio
    async def test_done_event_from_queue_closes_stream(self) -> None:
        """A `done` event on the channel is yielded once then the generator returns."""
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        gen = stream_operation_events(
            op_id,
            last_event_id=None,
            bus=bus,
            token_expires_at=None,
            heartbeat_s=10.0,  # long so ping doesn't fire first
        )

        async def _publisher() -> None:
            # Give the generator a chance to enter queue.get before we
            # publish; a zero-sleep yields the loop.
            await asyncio.sleep(0)
            await bus.publish(op_id, "done", {})

        publisher_task = asyncio.create_task(_publisher())
        try:
            frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        finally:
            publisher_task.cancel()
        assert b"event: done" in frame
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    @pytest.mark.asyncio
    async def test_auth_expired_has_monotonic_id(self) -> None:
        bus = OperationEventBus()
        op_id = uuid.uuid4()
        await bus.publish(op_id, "hold_point_changed", {"n": 1})
        gen = stream_operation_events(
            op_id,
            last_event_id=1,
            bus=bus,
            token_expires_at=100,
            now_fn=lambda: 200,  # "now" already past exp
            heartbeat_s=0.05,
        )
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert b"event: auth_expired" in frame
        assert b"id: 2" in frame
        # Generator must close after auth_expired.
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()


class TestDeferredPublish:
    @pytest.mark.asyncio
    async def test_queue_publish_does_not_fire_until_drain(self) -> None:
        """queue_publish defers the bus.publish; drain flushes."""
        from types import SimpleNamespace

        from forge.realtime import (
            drain_pending_publishes,
            queue_publish,
        )

        bus = OperationEventBus()
        op_id = uuid.uuid4()

        async def _send() -> None:
            await bus.publish(op_id, "hold_point_changed", {"n": 42})

        fake_session = SimpleNamespace(info={})
        queue_publish(fake_session, _send)

        # No fanout yet — no channel has been touched.
        assert op_id not in bus._channels

        await drain_pending_publishes(fake_session)

        assert op_id in bus._channels
        buffer = list(bus._channels[op_id].buffer)
        assert len(buffer) == 1
        assert buffer[0].data == {"n": 42}

    @pytest.mark.asyncio
    async def test_discard_pending_publishes_drops_queue(self) -> None:
        """discard_pending_publishes is the rollback-path equivalent."""
        from types import SimpleNamespace

        from forge.realtime import (
            discard_pending_publishes,
            drain_pending_publishes,
            queue_publish,
        )

        fired: list[int] = []

        async def _send() -> None:
            fired.append(1)

        fake_session = SimpleNamespace(info={})
        queue_publish(fake_session, _send)
        discard_pending_publishes(fake_session)
        await drain_pending_publishes(fake_session)
        assert fired == []
