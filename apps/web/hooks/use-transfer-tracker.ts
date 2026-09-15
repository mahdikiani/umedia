"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  getTransfer,
  isTransferInFlight,
  listTransfers,
  type TransferJob,
} from "@/lib/api";

const POLL_MS = 1000;
const SETTLED_DISMISS_MS = 5000;

/** Tracks library transfer jobs: seed in-flight on mount, poll while
 * queued|running, notify when settled, and auto-hide the banner 5s later. */
export function useTransferTracker(onSettled?: () => void) {
  const [jobs, setJobs] = useState<TransferJob[]>([]);
  const dismissedRef = useRef<Set<string>>(new Set());
  const dismissTimersRef = useRef<Map<string, number>>(new Map());
  const onSettledRef = useRef(onSettled);
  useEffect(() => {
    onSettledRef.current = onSettled;
  }, [onSettled]);

  const dismiss = useCallback((uid: string) => {
    const timer = dismissTimersRef.current.get(uid);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      dismissTimersRef.current.delete(uid);
    }
    dismissedRef.current.add(uid);
    setJobs((previous) => previous.filter((job) => job.uid !== uid));
  }, []);

  const scheduleAutoDismiss = useCallback(
    (uid: string) => {
      if (dismissTimersRef.current.has(uid)) return;
      const timer = window.setTimeout(() => {
        dismissTimersRef.current.delete(uid);
        dismiss(uid);
      }, SETTLED_DISMISS_MS);
      dismissTimersRef.current.set(uid, timer);
    },
    [dismiss],
  );

  const upsert = useCallback((job: TransferJob) => {
    if (dismissedRef.current.has(job.uid)) return;
    setJobs((previous) => {
      const index = previous.findIndex((item) => item.uid === job.uid);
      if (index === -1) return [...previous, job];
      const next = [...previous];
      next[index] = job;
      return next;
    });
  }, []);

  const track = useCallback(
    (job: TransferJob) => {
      dismissedRef.current.delete(job.uid);
      const timer = dismissTimersRef.current.get(job.uid);
      if (timer !== undefined) {
        window.clearTimeout(timer);
        dismissTimersRef.current.delete(job.uid);
      }
      upsert(job);
    },
    [upsert],
  );

  useEffect(() => {
    for (const job of jobs) {
      if (isTransferInFlight(job.status)) continue;
      scheduleAutoDismiss(job.uid);
    }
  }, [jobs, scheduleAutoDismiss]);

  useEffect(() => {
    const timers = dismissTimersRef.current;
    return () => {
      for (const timer of timers.values()) {
        window.clearTimeout(timer);
      }
      timers.clear();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void listTransfers()
      .then((listed) => {
        if (cancelled) return;
        for (const job of listed) {
          if (isTransferInFlight(job.status)) upsert(job);
        }
      })
      .catch(() => {
        // Non-fatal — panel stays empty until the user starts a transfer.
      });
    return () => {
      cancelled = true;
    };
  }, [upsert]);

  useEffect(() => {
    const active = jobs.filter((job) => isTransferInFlight(job.status));
    if (active.length === 0) return;

    let cancelled = false;
    const timer = window.setInterval(() => {
      void Promise.all(
        active.map(async (job) => {
          try {
            const latest = await getTransfer(job.uid);
            if (cancelled) return;
            upsert(latest);
            if (!isTransferInFlight(latest.status)) {
              onSettledRef.current?.();
            }
          } catch {
            // Leave the last known snapshot; next tick retries.
          }
        }),
      );
    }, POLL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobs, upsert]);

  return { jobs, track, dismiss };
}
