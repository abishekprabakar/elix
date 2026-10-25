import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

/** Measures an element's content width so SVG can be drawn at true pixel size. */
export function useWidth<T extends HTMLElement>(initial = 0) {
  const ref = useRef<T | null>(null);
  const [w, setW] = useState(initial);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth);
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setW(Math.round(e.contentRect.width));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/** Two key sequences like "g i" plus single keys. Ignores typing in inputs. */
export function useKeys(map: Record<string, (e: KeyboardEvent) => void>) {
  const pending = useRef<string | null>(null);
  const timer = useRef<number | null>(null);
  const mapRef = useRef(map);
  mapRef.current = map;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
      if (typing && e.key !== 'Escape') return;
      const key = e.key;
      if (pending.current) {
        const combo = `${pending.current} ${key}`;
        pending.current = null;
        if (timer.current) window.clearTimeout(timer.current);
        if (mapRef.current[combo]) {
          e.preventDefault();
          mapRef.current[combo](e);
          return;
        }
      }
      if (key === 'g') {
        pending.current = 'g';
        timer.current = window.setTimeout(() => (pending.current = null), 900);
        return;
      }
      if (mapRef.current[key]) {
        mapRef.current[key](e);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
}

/** j and k move a selection index through a list; Enter opens. */
export function useListKeys(count: number, onOpen: (i: number) => void, initial = -1) {
  const [index, setIndex] = useState(initial);
  const open = useRef(onOpen);
  open.current = onOpen;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'j') {
        setIndex((i) => Math.min(count - 1, i + 1));
      } else if (e.key === 'k') {
        setIndex((i) => Math.max(0, i - 1));
      } else if (e.key === 'Enter') {
        setIndex((i) => {
          if (i >= 0 && i < count) open.current(i);
          return i;
        });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [count]);
  return [index, setIndex] as const;
}

export function useCopy() {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = useCallback((text: string, key = 'x') => {
    const done = () => {
      setCopied(key);
      window.setTimeout(() => setCopied(null), 1600);
    };
    try {
      navigator.clipboard.writeText(text).then(done, () => fallback(text, done));
    } catch {
      fallback(text, done);
    }
  }, []);
  return { copied, copy };
}

function fallback(text: string, done: () => void) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand('copy');
    done();
  } catch {
    /* ignore */
  }
  document.body.removeChild(ta);
}
