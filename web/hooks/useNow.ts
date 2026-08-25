"use client";

import { useEffect, useState } from "react";

/**
 * A Date that ticks every `intervalMs`. Used to recompute relative
 * timestamps ("2m ago") and deadline countdowns client-side without
 * refetching data — motion on screen that's always honest about the wall
 * clock, independent of how often the underlying data actually changes.
 */
export function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
