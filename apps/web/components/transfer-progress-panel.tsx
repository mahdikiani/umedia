"use client";

import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { isTransferInFlight, type TransferJob } from "@/lib/api";

type TransferProgressPanelProps = {
  jobs: TransferJob[];
  onCancel: (uid: string) => void;
  onDismiss: (uid: string) => void;
};

function statusLabel(job: TransferJob): string {
  if (job.status === "cancelling") return "Cancelling…";
  if (job.status === "cancelled") return "Cancelled";
  if (isTransferInFlight(job.status)) {
    return `${job.progress_pct}%`;
  }
  if (job.status === "completed") return "Done";
  if (job.status === "partial") return "Partial";
  if (job.status === "failed") return "Failed";
  return job.status;
}

export function TransferProgressPanel({
  jobs,
  onCancel,
  onDismiss,
}: TransferProgressPanelProps) {
  if (jobs.length === 0) return null;

  return (
    <div
      aria-live="polite"
      className="space-y-3 rounded-xl border p-4"
      data-testid="transfer-progress"
    >
      {jobs.map((job) => {
        const inFlight = isTransferInFlight(job.status);
        const label =
          job.current_name ??
          (job.operation === "copy" ? "Copying…" : "Moving…");
        return (
          <div className="space-y-1.5" key={job.uid}>
            <div className="flex items-center justify-between gap-3 text-sm">
              <div className="min-w-0">
                <span className="me-2 capitalize text-muted-foreground">
                  {job.operation}
                </span>
                <span className="truncate">{label}</span>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <span className="text-muted-foreground">
                  {statusLabel(job)}
                </span>
                {job.status === "queued" || job.status === "running" ? (
                  <Button
                    aria-label="Cancel transfer"
                    onClick={() => onCancel(job.uid)}
                    size="xs"
                    variant="ghost"
                  >
                    Cancel
                  </Button>
                ) : null}
                {!inFlight ? (
                  <Button
                    aria-label="Dismiss transfer"
                    onClick={() => onDismiss(job.uid)}
                    size="icon-xs"
                    variant="ghost"
                  >
                    <X size={12} />
                  </Button>
                ) : null}
              </div>
            </div>
            <Progress value={inFlight ? job.progress_pct : 100} />
            {job.error ? (
              <p className="text-xs text-destructive">{job.error}</p>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
