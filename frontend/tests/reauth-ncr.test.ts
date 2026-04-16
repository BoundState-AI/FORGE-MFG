/**
 * Module:   NCR disposition same-key retry test
 * Purpose:  Pin that ``dispositionNCR`` forwards a caller-supplied
 *           Idempotency-Key on the PATCH request — the GC-3
 *           spec §9.3 line 1490 retry contract.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why its own file: the main reauth test file installs a global
 * ``vi.mock('@/api/endpoints', ...)`` to stub ``createSignOff`` for
 * the SignOffPanel tests.  That mock intercepts any import from that
 * module, including ``dispositionNCR`` — which means we cannot test
 * the real ``dispositionNCR`` wrapper in the same file.  A separate
 * file with no endpoints mock gives us the real wrapper plus a spy
 * on the lower-level typedClient so the assertion lands on the
 * actual header payload.
 */

import { describe, it, expect, vi } from 'vitest';
import { typedClient } from '@/api/typed-client';
import { dispositionNCR } from '@/api/endpoints';

describe('dispositionNCR same-key retry contract', () => {
  it('forwards a caller-supplied Idempotency-Key to the PATCH request', async () => {
    /* Spy on the generated-client PATCH method.  We don't care about
       the full response shape — just that our header reached it. */
    const patchSpy = vi.spyOn(typedClient, 'PATCH').mockResolvedValue({
      data: {
        id: 'ncr-1',
        ncr_number: 'NCR-2026-0001',
        work_order_id: 'wo-1',
        operation_id: null,
        part_instance_id: null,
        description: 'x',
        root_cause: null,
        status: 'dispositioned',
        disposition: 'use_as_is',
        disposition_rationale: 'ok',
        engineering_approval_id: null,
        mrb_required: false,
        mrb_disposition: null,
        mrb_authority_id: null,
        schedule_impact_days: null,
        initiated_by_id: 'u1',
        evidence_refs: [],
        remediation_work_order_id: null,
        version: 2,
        created_at: '2026-04-16T00:00:00Z',
        updated_at: '2026-04-16T00:00:00Z',
      },
      error: undefined,
      response: new Response('', { status: 200 }),
    } as never);

    await dispositionNCR(
      'ncr-1',
      {
        version: 1,
        disposition: 'use_as_is',
        disposition_rationale: 'ok',
      },
      'retry-key-fixed',
    );

    expect(patchSpy).toHaveBeenCalledTimes(1);
    const callArgs = patchSpy.mock.calls[0] as [
      unknown,
      { headers?: Record<string, string> },
    ];
    expect(callArgs[1].headers?.['Idempotency-Key']).toBe('retry-key-fixed');

    patchSpy.mockRestore();
  });

  it('omits the Idempotency-Key header when no key is supplied', async () => {
    /* First-attempt path: the caller passes no key, and the typedClient
       middleware auto-generates one.  The wrapper itself must NOT set
       the header in this case — doing so would double up with the
       middleware's auto-gen. */
    const patchSpy = vi.spyOn(typedClient, 'PATCH').mockResolvedValue({
      data: {} as never,
      error: undefined,
      response: new Response('', { status: 200 }),
    } as never);

    await dispositionNCR('ncr-1', {
      version: 1,
      disposition: 'use_as_is',
      disposition_rationale: 'ok',
    });

    const callArgs = patchSpy.mock.calls[0] as [
      unknown,
      { headers?: unknown },
    ];
    expect(callArgs[1].headers).toBeUndefined();

    patchSpy.mockRestore();
  });
});
