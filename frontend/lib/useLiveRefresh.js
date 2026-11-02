'use client';

import { useEffect, useRef } from 'react';

const LEADS_CHANGED = 'crm:leads-changed';

/** Tell lead screens in this tab that a lead moved, so they reload without waiting for the timer. */
export function announceLeadsChanged() {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(LEADS_CHANGED));
}

/** Reload on a timer while the tab is visible, when the tab comes back, and when a lead moves here. */
export function useLiveRefresh(refresh, intervalMs = 30000) {
  const latest = useRef(refresh);
  latest.current = refresh;

  useEffect(() => {
    const whenVisible = () => {
      if (document.visibilityState === 'visible') latest.current();
    };
    const now = () => latest.current();
    const timer = setInterval(whenVisible, intervalMs);
    document.addEventListener('visibilitychange', whenVisible);
    window.addEventListener('focus', whenVisible);
    window.addEventListener(LEADS_CHANGED, now);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', whenVisible);
      window.removeEventListener('focus', whenVisible);
      window.removeEventListener(LEADS_CHANGED, now);
    };
  }, [intervalMs]);
}
