import { useEffect, useRef, useState } from "react";
import { drafts } from "./idb";

/** State that is also saved to IndexedDB on this device, so a half-entered form survives
 * a crash, reload or lost connection. Returns [value, setValue, clear, restored]. */
export function useDraft<T>(key: string, initial: T) {
  const [value, setValueRaw] = useState<T>(initial);
  const [restored, setRestored] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const dirty = useRef(false);
  useEffect(() => {
    let alive = true;
    drafts.get<{ v: T; at: number }>(key).then((d) => {
      if (!alive) return;
      // never overwrite something the user already typed while storage was opening
      if (d?.v && !dirty.current) { setValueRaw(d.v); setRestored(true); }
      setLoaded(true);
    });
    return () => { alive = false; };
  }, [key]);
  useEffect(() => {
    if (!loaded) return;
    const t = setTimeout(() => drafts.set(key, { v: value, at: Date.now() }), 250);
    return () => clearTimeout(t);
  }, [key, value, loaded]);
  const setValue = (v: T) => { dirty.current = true; setValueRaw(v); };
  const clear = () => { drafts.del(key); setRestored(false); dirty.current = false; };
  return [value, setValue, clear, restored] as const;
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}
