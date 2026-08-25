"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface PollingResult<T> {
  data: T | null;
  /** True only until the FIRST successful load — never true again on
   *  subsequent polls, which is what keeps the stats banner from reflowing
   *  into a skeleton every few seconds while numbers tick up on camera. */
  initialLoading: boolean;
  error: string | null;
  /** True while a background refetch is in flight after the first load. */
  refreshing: boolean;
  refresh: () => void;
}

/**
 * Polls `fetcher` every `intervalMs`. Data is only ever replaced by a
 * successful response — a transient error leaves the last good value on
 * screen rather than clearing it, so a flaky poll never blanks the banner
 * mid-demo.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  deps: unknown[] = []
): PollingResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const loadedOnce = useRef(false);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    if (loadedOnce.current) setRefreshing(true);
    try {
      const result = await fetcherRef.current();
      setData(result);
      setError(null);
      loadedOnce.current = true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    } finally {
      setInitialLoading(false);
      setRefreshing(false);
      inFlight.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadedOnce.current = false;
    setInitialLoading(true);
    load();
    const id = setInterval(load, intervalMs);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, load, ...deps]);

  return { data, initialLoading, error, refreshing, refresh: load };
}
