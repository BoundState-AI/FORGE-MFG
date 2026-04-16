/**
 * Module:   useOperationSSE
 * Purpose:  SSE subscription for the Operation Execution page.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why fetch-based SSE: Spec §6.5.2 requires bearer auth on the SSE
 * connection.  Native EventSource does not support custom headers, so
 * we use fetch + ReadableStream to consume the event stream.
 *
 * Reconnect contract: On every reconnect (heartbeat timeout, network
 * drop, auth refresh), the caller MUST refetch the full operation state
 * via GET /api/operations/:opId.  SSE replay via Last-Event-ID is
 * best-effort only; the REST refetch is always authoritative.
 */

import { useEffect, useRef, useCallback } from 'react';
import type {
  SSEHoldPointChanged,
  SSEWoStateChanged,
  SSELaborActivity,
  SSEOperationReopened,
} from '@/types/api';

const HEARTBEAT_TIMEOUT_MS = 40_000;
const BACKOFF_BASE_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;

interface SSECallbacks {
  onHoldPointChanged?: (data: SSEHoldPointChanged) => void;
  onWoStateChanged?: (data: SSEWoStateChanged) => void;
  onLaborActivity?: (data: SSELaborActivity) => void;
  onOperationReopened?: (data: SSEOperationReopened) => void;
  onReconnect: () => void;
  onConnectionLost?: () => void;
  onConnectionRestored?: () => void;
}

export function useOperationSSE(
  operationId: string | undefined,
  callbacks: SSECallbacks,
) {
  const abortRef = useRef<AbortController | null>(null);
  const heartbeatRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryCountRef = useRef(0);
  const lastEventIdRef = useRef<string | null>(null);
  const connectedRef = useRef(false);

  const clearHeartbeat = useCallback(() => {
    if (heartbeatRef.current) {
      clearTimeout(heartbeatRef.current);
      heartbeatRef.current = null;
    }
  }, []);

  const resetHeartbeat = useCallback(() => {
    clearHeartbeat();
    heartbeatRef.current = setTimeout(() => {
      /* No ping in 40s — connection dead, trigger reconnect */
      callbacks.onConnectionLost?.();
      abortRef.current?.abort();
    }, HEARTBEAT_TIMEOUT_MS);
  }, [clearHeartbeat, callbacks]);

  const connect = useCallback(async () => {
    if (!operationId) return;

    const controller = new AbortController();
    abortRef.current = controller;

    const token = sessionStorage.getItem('forge_access_token');
    const headers: Record<string, string> = {
      Accept: 'text/event-stream',
    };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (lastEventIdRef.current) {
      headers['Last-Event-ID'] = lastEventIdRef.current;
    }

    const programId = sessionStorage.getItem('forge_active_program');
    if (programId) headers['X-Program-Id'] = programId;

    try {
      const response = await fetch(
        `/api/operations/${operationId}/events`,
        {
          headers,
          signal: controller.signal,
        },
      );

      if (!response.ok || !response.body) {
        throw new Error(`SSE connect failed: ${response.status}`);
      }

      /* Connected successfully — reset backoff */
      retryCountRef.current = 0;
      if (!connectedRef.current) {
        connectedRef.current = true;
        callbacks.onConnectionRestored?.();
      }

      /* Mandatory REST refetch on every connect/reconnect */
      callbacks.onReconnect();
      resetHeartbeat();

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        let currentEvent = '';
        let currentData = '';
        let currentId = '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6);
          } else if (line.startsWith('id: ')) {
            currentId = line.slice(4).trim();
          } else if (line === '') {
            /* End of event — dispatch */
            if (currentId) lastEventIdRef.current = currentId;
            resetHeartbeat();

            if (currentEvent && currentData) {
              handleEvent(currentEvent, currentData, callbacks);
            }
            currentEvent = '';
            currentData = '';
            currentId = '';
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return; /* Clean close */
      }
      connectedRef.current = false;
      callbacks.onConnectionLost?.();
    }

    clearHeartbeat();

    /* Reconnect with exponential backoff */
    if (!controller.signal.aborted) {
      const delay = Math.min(
        BACKOFF_BASE_MS * 2 ** retryCountRef.current,
        BACKOFF_MAX_MS,
      );
      retryCountRef.current++;
      setTimeout(() => connect(), delay);
    }
  }, [operationId, callbacks, resetHeartbeat, clearHeartbeat]);

  useEffect(() => {
    connect();
    return () => {
      abortRef.current?.abort();
      clearHeartbeat();
    };
  }, [connect, clearHeartbeat]);
}

function handleEvent(
  event: string,
  data: string,
  callbacks: SSECallbacks,
): void {
  try {
    switch (event) {
      case 'ping':
        /* Heartbeat — resetHeartbeat already called */
        break;
      case 'buffer_reset':
        /* REST refetch is always performed on reconnect; nothing else needed */
        break;
      case 'auth_expired':
        /* Token refresh needed — for now log; GC-3 will implement re-auth */
        console.warn('[SSE] auth_expired — token refresh needed');
        break;
      case 'done':
        break;
      case 'error':
        console.error('[SSE] server error:', data);
        break;
      case 'hold_point_changed':
        callbacks.onHoldPointChanged?.(JSON.parse(data));
        break;
      case 'wo_state_changed':
        callbacks.onWoStateChanged?.(JSON.parse(data));
        break;
      case 'labor_activity':
        callbacks.onLaborActivity?.(JSON.parse(data));
        break;
      case 'operation_reopened':
        callbacks.onOperationReopened?.(JSON.parse(data));
        break;
    }
  } catch (err) {
    console.error('[SSE] Failed to parse event:', event, err);
  }
}
