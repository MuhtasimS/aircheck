'use client';

import { useEffect, useRef, useState } from 'react';

export interface ResourceState<T> {
  status: 'loading' | 'ready' | 'error';
  data: T | null;
  error: string | null;
}

/**
 * Fetch a projection from the API, expose explicit loading / ready / error
 * states, and (optionally) poll while the runtime is still doing work.
 *
 * Callers pass a memoized `fetcher` (stable identity via `useCallback`) and,
 * when polling should stop at a terminal state, a stable `shouldPoll`
 * predicate. Polling ceases as soon as `shouldPoll(data)` is false, so a
 * finished run does not keep hitting the API. A transient poll failure keeps the
 * last good data on screen while surfacing the error rather than blanking a
 * working view.
 */
export function usePolledResource<T>(
  fetcher: () => Promise<T>,
  options: { intervalMs?: number; shouldPoll?: (data: T) => boolean } = {},
): ResourceState<T> {
  const { intervalMs = 3000, shouldPoll } = options;
  const [state, setState] = useState<ResourceState<T>>({
    status: 'loading',
    data: null,
    error: null,
  });
  const dataRef = useRef<T | null>(null);

  useEffect(() => {
    let active = true;

    const load = async () => {
      try {
        const data = await fetcher();
        if (!active) return;
        dataRef.current = data;
        setState({ status: 'ready', data, error: null });
      } catch (err) {
        if (!active) return;
        const message =
          err instanceof Error ? err.message : 'Unexpected error.';
        // Keep the last good data on screen (dataRef) while surfacing the error.
        setState({ status: 'error', data: dataRef.current, error: message });
      }
    };

    void load();

    const id =
      intervalMs > 0
        ? setInterval(() => {
            const current = dataRef.current;
            const keepPolling =
              current === null ||
              shouldPoll === undefined ||
              shouldPoll(current);
            if (keepPolling) void load();
          }, intervalMs)
        : undefined;

    return () => {
      active = false;
      if (id !== undefined) clearInterval(id);
    };
  }, [fetcher, intervalMs, shouldPoll]);

  return state;
}
