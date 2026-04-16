/**
 * Module:   Test Setup
 * Purpose:  Vitest global setup — DOM matchers and MSW.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 */

import '@testing-library/jest-dom/vitest';

/* Mock sessionStorage for tests */
const store: Record<string, string> = {};
const mockStorage: Storage = {
  getItem: (key: string) => store[key] ?? null,
  setItem: (key: string, value: string) => { store[key] = value; },
  removeItem: (key: string) => { delete store[key]; },
  clear: () => { Object.keys(store).forEach((k) => delete store[k]); },
  get length() { return Object.keys(store).length; },
  key: (i: number) => Object.keys(store)[i] ?? null,
};

Object.defineProperty(window, 'sessionStorage', { value: mockStorage });
Object.defineProperty(window, 'localStorage', { value: mockStorage });
