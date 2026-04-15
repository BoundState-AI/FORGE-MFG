#!/usr/bin/env python3
"""
Module:   Realtime Event Bus
Purpose:  Per-operation in-memory event bus backing the Operation
          Execution SSE channel (UX spec v1.5.1 §6.5.2).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Spec §6.5.2 is explicit that the Operation Execution SSE channel
  is best-effort in-memory only (the v1.4 "at-least-once" claim was
  retracted by R4-004).  The authoritative reconcile path is the
  REST refetch of GET /api/operations/:opId on every reconnect.
  This module provides just enough of a publisher / subscriber
  layer to satisfy the spec surface — a bounded ring buffer for
  `Last-Event-ID` replay, a monotonic id per op channel, fan-out
  to subscribers via asyncio queues, and a `buffer_reset` signal
  when replay cannot be satisfied.

  Per-operation channels instead of a global bus: the frontend
  subscribes by op_id, so the natural key is op_id.  Keeping each
  channel's id counter independent means a hot op does not burn
  buffer room in a cold op's window, and a quiet op's replay
  stays coherent even while the loud op rolls its ring.

  Buffer sized at 256 per op.  Large enough to cover a short
  network blip (seconds of hold/resume/labor churn), small enough
  that memory cost stays O(ops * 256 * event_size) — meaningful
  only if thousands of ops are subscribed simultaneously, which is
  not the phase-1 load shape.

  No durability: process restart / deploy loses all buffers; the
  spec treats this as expected and leans on the REST refetch.

  auth_expired: the subscriber loop consults the caller's
  AuthContext.expires_at on every heartbeat tick.  When the token
  has expired the server emits `event: auth_expired` and closes
  the stream.  The client's reconnect path refreshes the token
  and re-runs the REST refetch, so missed events in between are
  covered by the refetch, not the buffer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid as _uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models.operation import Operation
from forge.models.user import User

logger = logging.getLogger(__name__)

# Per-channel buffer size.  See module docstring for the tradeoff.
_BUFFER_SIZE = 256

# Heartbeat cadence (seconds).  Spec §6.5.2 "every 20 seconds".
HEARTBEAT_INTERVAL_S = 20.0


def _resolve_heartbeat_s() -> float:
    """Current heartbeat cadence, honoring the FORGE_SSE_HEARTBEAT_S override.

    Resolved at call-time (not module-load) so integration tests can
    monkeypatch the env var per-test to a tight cadence (e.g. 0.1s)
    so the queue.get() wait unblocks fast enough to propagate a
    client disconnect before the test's patience expires.  Bad
    values fall back silently to the spec default — the contract
    only cares that the cadence exists, not its exact value.
    """
    raw = os.getenv("FORGE_SSE_HEARTBEAT_S")
    if raw is None:
        return HEARTBEAT_INTERVAL_S
    try:
        return float(raw)
    except ValueError:
        return HEARTBEAT_INTERVAL_S

# Per-subscriber queue cap.  Slow consumers that fall this far behind
# are dropped — the reconnect refetch will reconcile.  Keeps a blocked
# client from pinning memory indefinitely.
_SUBSCRIBER_QUEUE_MAX = 512


@dataclass(frozen=True, slots=True)
class OperationEvent:
    """A single event on a per-operation channel.

    id: monotonic integer within the channel (spec §6.5.2 id header).
    event: SSE event name (hold_point_changed, wo_state_changed, etc).
    data: serializable payload the client receives as the data field.
    """

    id: int
    event: str
    data: dict[str, Any]

    def to_sse_frame(self) -> bytes:
        """Serialize to the wire-frame shape the spec defines."""
        payload = json.dumps(self.data, separators=(",", ":"))
        return (
            f"event: {self.event}\nid: {self.id}\ndata: {payload}\n\n".encode()
        )


@dataclass
class _OpChannel:
    """In-memory state for one operation's event stream.

    counter: next id to assign (incremented on any emit, buffered
      or control).  One shared monotonic sequence covers real events
      AND control frames (ping / buffer_reset / auth_expired / error),
      matching the spec §6.5.2 "each SSE event carries an id" rule.
      Control frames don't enter the replay buffer — they're
      transient — but their ids still land above real-event ids, and
      a client reconnecting with a control-event hint replays
      nothing because none of the buffered real events have id
      greater than the hint.
    buffer: bounded ring of recent real events for `Last-Event-ID`
      replay.  Control frames are intentionally excluded.
    subscribers: active asyncio.Queue list; fan-out on publish.
    """

    counter: int = 0
    buffer: deque[OperationEvent] = field(
        default_factory=lambda: deque(maxlen=_BUFFER_SIZE),
    )
    subscribers: list[asyncio.Queue[OperationEvent]] = field(
        default_factory=list,
    )


class OperationEventBus:
    """In-process pub/sub for operation SSE events.

    The bus is a singleton module-global (`OPERATION_BUS`).  Using a
    class keeps the surface testable — tests can construct an isolated
    bus when they want full control, and the module-level global is
    the default for production wiring.
    """

    def __init__(self) -> None:
        self._channels: dict[_uuid.UUID, _OpChannel] = {}
        # One lock guards channel creation + subscriber list mutation.
        # Per-channel locking is not needed at the expected load
        # (tens of active ops per floor).
        self._lock = asyncio.Lock()

    def _get_or_create(self, op_id: _uuid.UUID) -> _OpChannel:
        ch = self._channels.get(op_id)
        if ch is None:
            ch = _OpChannel()
            self._channels[op_id] = ch
        return ch

    async def publish(
        self,
        op_id: _uuid.UUID,
        event: str,
        data: dict[str, Any],
    ) -> OperationEvent:
        """Append an event to the channel buffer and fan out to subscribers.

        Returns the event so callers that want the generated id (for
        logging / assertions) can see it without a second lookup.
        Dropped-subscriber cleanup happens inside the same lock so the
        subscriber list does not mutate mid-iteration on a parallel
        subscribe / publish race.
        """
        async with self._lock:
            ch = self._get_or_create(op_id)
            ch.counter += 1
            evt = OperationEvent(id=ch.counter, event=event, data=data)
            ch.buffer.append(evt)
            # Copy the subscriber list under the lock, push outside —
            # keeps the critical section tight.
            live: list[asyncio.Queue[OperationEvent]] = []
            for queue in ch.subscribers:
                try:
                    queue.put_nowait(evt)
                    live.append(queue)
                except asyncio.QueueFull:
                    logger.warning(
                        "SSE subscriber on op=%s queue full; dropping "
                        "connection, relying on REST refetch",
                        op_id,
                    )
            ch.subscribers = live
        return evt

    async def subscribe(
        self,
        op_id: _uuid.UUID,
        last_event_id: int | None = None,
    ) -> tuple[list[OperationEvent], asyncio.Queue[OperationEvent], bool]:
        """Register a subscriber and compute the replay slice.

        Returns (replay_events, live_queue, replay_ok).
        - replay_events: buffered events with id > last_event_id, in
          id order.  Empty on first connect (no hint) or on buffer miss.
        - live_queue: queue the stream loop reads from for fresh events.
        - replay_ok: True when the requested last_event_id is replayable
          from the current buffer.  False signals the route to emit
          `buffer_reset` before live events — spec §6.5.2 buffer miss.

        replay_ok is True when last_event_id is None (first connect —
        nothing to replay, but no reset either) OR when the hint lies
        within the buffer's covered range.  It is False when the hint
        precedes the buffer's oldest id (the ring has rolled past the
        client's last known position, so the client must rely on the
        REST refetch).
        """
        async with self._lock:
            ch = self._get_or_create(op_id)
            queue: asyncio.Queue[OperationEvent] = asyncio.Queue(
                maxsize=_SUBSCRIBER_QUEUE_MAX,
            )
            ch.subscribers.append(queue)

            replay_ok = True
            replay: list[OperationEvent] = []
            if last_event_id is not None:
                if not ch.buffer:
                    # Nothing in buffer means nothing to replay; if the
                    # client's hint is ahead of our counter we are in
                    # a reset scenario, but we treat any non-empty
                    # hint against an empty buffer as "need refetch"
                    # because we genuinely cannot prove the events are
                    # still covered.
                    replay_ok = last_event_id == ch.counter
                else:
                    oldest = ch.buffer[0].id
                    if last_event_id < oldest - 1:
                        # Ring has rolled past; client's last id is
                        # gone.  Replay not satisfiable.
                        replay_ok = False
                    else:
                        replay = [
                            e for e in ch.buffer if e.id > last_event_id
                        ]
            return replay, queue, replay_ok

    async def unsubscribe(
        self, op_id: _uuid.UUID, queue: asyncio.Queue[OperationEvent],
    ) -> None:
        """Remove a subscriber queue on disconnect / abort."""
        async with self._lock:
            ch = self._channels.get(op_id)
            if ch is None:
                return
            try:
                ch.subscribers.remove(queue)
            except ValueError:
                # Already removed (publish-dropped or race); no-op.
                return

    async def next_control_id(self, op_id: _uuid.UUID) -> int:
        """Advance the channel counter for a control frame.

        Control frames (ping, buffer_reset, auth_expired, error)
        carry a monotonic id per spec §6.5.2 but are NOT stored in
        the replay buffer — they are transient.  Sharing the
        counter with real events means ids stay strictly monotonic
        from the client's view; a stale Last-Event-ID pointing at a
        control frame replays nothing (no buffered events exceed
        the hint).
        """
        async with self._lock:
            ch = self._get_or_create(op_id)
            ch.counter += 1
            return ch.counter

    def _reset(self) -> None:
        """Drop all channels.  Test-only hook — production wiring
        carries state for the lifetime of the process."""
        self._channels.clear()


# Module-level singleton used by publishers and the SSE route.
# Tests that want isolation can instantiate OperationEventBus() directly.
OPERATION_BUS = OperationEventBus()


# ---------------------------------------------------------------------------
# Deferred publish — fire SSE events AFTER the transaction commits
# ---------------------------------------------------------------------------


_PendingKey = "_realtime_pending"


def queue_publish(
    session: AsyncSession,
    coro_factory: Callable[[], Awaitable[Any]],
) -> None:
    """Register a publish to fire after the session's current tx commits.

    SSE subscribers must never observe state that has not yet
    committed — the v1.5.1 §6.5.2 channel is treated as a truthful
    read-side by clients.  Services queue their publishes here; the
    route layer drains after a successful `session.commit()` via
    `drain_pending_publishes(session)`.  If the commit is rolled
    back, the queued publishes are dropped (the finally-drain the
    route does pops them) — no subscriber ever sees the phantom
    state.

    `coro_factory` is a zero-arg callable that returns a coroutine
    so the queue stores cheap closures instead of live coroutines
    (a live coroutine that never awaits emits a RuntimeWarning).
    """
    pending: list[Callable[[], Awaitable[Any]]] = session.info.setdefault(
        _PendingKey, [],
    )
    pending.append(coro_factory)


async def drain_pending_publishes(session: AsyncSession) -> None:
    """Fire every queued publish.  Called by routes after commit.

    Each publish runs independently — a failure in one does not
    block the rest.  Per-publish failures are logged and swallowed
    because the SSE channel is best-effort: the client's mandatory
    REST refetch on reconnect remains the authoritative state
    surface, so a missed event only costs the sub-second visibility
    window, not correctness.
    """
    pending: list[Callable[[], Awaitable[Any]]] = session.info.pop(
        _PendingKey, [],
    )
    for factory in pending:
        try:
            await factory()
        except Exception:
            logger.warning(
                "SSE publish failed post-commit; REST refetch will "
                "reconcile",
                exc_info=True,
            )


def discard_pending_publishes(session: AsyncSession) -> None:
    """Drop queued publishes without firing them.

    Used by the route layer on rollback paths so a failed mutation
    never surfaces as a live SSE event.  Exception handlers in the
    route layer call this before re-raising.
    """
    session.info.pop(_PendingKey, None)


# ---------------------------------------------------------------------------
# Actor-resolution helper (spec §6.5.2 actor.display_name)
# ---------------------------------------------------------------------------


async def resolve_actor(
    session: AsyncSession, user_id: _uuid.UUID,
) -> dict[str, str]:
    """Return the actor dict the spec names in SSE payloads.

    Shape: {"id": <uuid str>, "display_name": <str>}.  `id` is the
    UUID as string — the spec uses "id" for labor_activity and
    "user_id" for wo_state_changed; callers pick the right key per
    event type.  The display_name is resolved via a single Users
    select; a missing user row degrades gracefully to the sub string
    so the stream remains useful even with a stale audit seed.
    """
    result = await session.execute(
        select(User.display_name).where(User.id == user_id),
    )
    name = result.scalar_one_or_none()
    return {"id": str(user_id), "display_name": name or str(user_id)}


async def publish_labor_activity(
    session: AsyncSession,
    op_id: _uuid.UUID,
    actor_id: _uuid.UUID,
    action: str,
    entry_id: _uuid.UUID,
) -> None:
    """Queue a §6.5.2 labor_activity publish for post-commit delivery.

    Resolves actor display_name EAGERLY (one User lookup under the
    live transaction) so the post-commit publish carries the
    transaction-consistent actor snapshot.  The bus hit itself is
    deferred via queue_publish — subscribers must never observe a
    labor transition that is still rolled-backable.

    Used by every labor start / stop / force-stop / cascade-stop
    path so the Operation Execution subscriber sees other users'
    timer changes without a poll.
    """
    actor = await resolve_actor(session, actor_id)
    payload = {
        "operation_id": str(op_id),
        "actor": actor,
        "action": action,
        "entry_id": str(entry_id),
    }
    queue_publish(
        session,
        lambda: OPERATION_BUS.publish(op_id, "labor_activity", payload),
    )


async def publish_wo_state_changed_to_ops(
    session: AsyncSession,
    wo_id: _uuid.UUID,
    payload: dict[str, Any],
) -> None:
    """Queue a wo_state_changed fan-out for post-commit delivery.

    The SSE contract is keyed per op_id (UX spec §6.5.2), so a
    single parent-WO transition must be broadcast to all child op
    channels the frontend might be subscribed to.  Op-id lookup
    runs eagerly against the live transaction; the bus hit per op
    is deferred so a rolled-back WO transition never surfaces on
    the client stream.  Each per-op publish picks up its own
    monotonic id within that channel; ordering within the stream
    is preserved for each subscriber independently.

    Callers should pass a payload dict that matches the §6.5.2
    schema for wo_state_changed (work_order_id, state,
    previous_state, actor, reason, force_stop_labor,
    stopped_labor_entries).
    """
    result = await session.execute(
        select(Operation.id).where(Operation.work_order_id == wo_id),
    )
    op_ids = [row[0] for row in result.all()]
    for target_op_id in op_ids:
        # Default-arg trick binds op_id per iteration; a naked
        # closure over the loop var would capture the last value
        # for every factory.
        def _make_publish(
            op: _uuid.UUID = target_op_id,
        ) -> Awaitable[OperationEvent]:
            return OPERATION_BUS.publish(op, "wo_state_changed", payload)

        queue_publish(session, _make_publish)


# ---------------------------------------------------------------------------
# SSE stream generator used by GET /api/operations/{op_id}/events
# ---------------------------------------------------------------------------


async def stream_operation_events(
    op_id: _uuid.UUID,
    *,
    last_event_id: int | None,
    bus: OperationEventBus,
    token_expires_at: int | None,
    now_fn: Any = time.time,
    heartbeat_s: float | None = None,
) -> AsyncIterator[bytes]:
    """Yield SSE frames for the operation event stream.

    Contract (UX spec v1.5.1 §6.5.2):
      1. If last_event_id is given and the buffer cannot satisfy it,
         emit `buffer_reset` first.
      2. Replay any buffered events the client missed.
      3. Loop:
         - wait up to `heartbeat_s` for the next event.
         - emit event when available.
         - emit `ping` on timeout.
         - if `token_expires_at` has passed, emit `auth_expired` and
           close cleanly.
         - on an internal failure in the event loop, emit `error`
           with a {code, message} payload and close cleanly.
      4. On client abort, the cancellation propagates and we
         unsubscribe the queue.

    Every frame carries a monotonic id sourced from the channel
    counter — including control frames.  Control frames do NOT
    enter the replay buffer; their ids are issued strictly to keep
    the client's id sequence monotonic per the spec's `id:
    <monotonic-int>` rule.

    `done` is emitted only as an explicit server-triggered close
    signal (UX spec §6.5.2 "Completion: a done event marks clean
    completion").  The current trigger is parent-WO close: when
    the WO transitions to `closed`, work_order_service publishes a
    `done` event onto every child op channel so subscribers stop
    reconnecting on a stream that will never emit again.  Any
    event with name `done` that arrives from the queue is yielded
    once and the generator returns.

    `now_fn` + `heartbeat_s` are parameterized so tests can
    exercise ping / auth_expired / error paths deterministically.
    """
    # Resolve heartbeat at call-time so a mid-process env-var tweak
    # is picked up without restarting the process.
    if heartbeat_s is None:
        heartbeat_s = _resolve_heartbeat_s()

    replay, queue, replay_ok = await bus.subscribe(
        op_id, last_event_id=last_event_id,
    )

    if not replay_ok:
        reset_id = await bus.next_control_id(op_id)
        reset = OperationEvent(
            id=reset_id,
            event="buffer_reset",
            data={"reason": "events_unavailable"},
        )
        yield reset.to_sse_frame()

    for evt in replay:
        yield evt.to_sse_frame()

    try:
        while True:
            if token_expires_at is not None and now_fn() >= token_expires_at:
                exp_id = await bus.next_control_id(op_id)
                expired = OperationEvent(
                    id=exp_id, event="auth_expired", data={},
                )
                yield expired.to_sse_frame()
                return
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                ping_id = await bus.next_control_id(op_id)
                ping = OperationEvent(id=ping_id, event="ping", data={})
                yield ping.to_sse_frame()
                continue
            except asyncio.CancelledError:
                # Client abort — propagate so the outer StreamingResponse
                # cleans up; the finally block still unsubscribes.
                raise
            except Exception as exc:  # pragma: no cover - defensive
                # Internal failure on the event loop.  Surface to the
                # client via the spec §6.5.2 `error` event so the
                # frontend can switch to its 30s polling fallback
                # ("Real-time updates unavailable — refreshing every 30 s")
                # rather than looping reconnects.  Close cleanly after.
                err_id = await bus.next_control_id(op_id)
                err = OperationEvent(
                    id=err_id, event="error",
                    data={
                        "code": "SSE_INTERNAL",
                        "message": str(exc) or "Stream error",
                    },
                )
                yield err.to_sse_frame()
                return
            if evt.event == "done":
                # Server-triggered clean completion (spec §6.5.2):
                # emit once and close the generator.  Upstream
                # publisher decides when this fires; the current
                # trigger is parent-WO close.
                yield evt.to_sse_frame()
                return
            yield evt.to_sse_frame()
    finally:
        await bus.unsubscribe(op_id, queue)
