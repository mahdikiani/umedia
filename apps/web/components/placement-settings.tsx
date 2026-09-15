"use client";

import { ChevronDown, ChevronUp, HardDrive } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  api,
  type PlacementPolicy,
  type PlacementSettings,
  type ProviderConnection,
} from "@/lib/api";

const AUTO_DEFAULT = "__auto__";

const POLICY_LABELS: Record<PlacementPolicy, string> = {
  default: "Default storage",
  fill_order: "Fill in order",
  most_free: "Most free space",
};

const POLICY_HELP: Record<PlacementPolicy, string> = {
  default: "New files and folders at the library root go to the default storage.",
  fill_order: "Try storages in this order until one is available.",
  most_free:
    "Prefer the storage with the most free space. Until plugins report capacity, this follows fill order.",
};

function orderedConnections(
  connections: ProviderConnection[],
  fillOrder: string[],
): ProviderConnection[] {
  const byId = new Map(connections.map((connection) => [connection.uid, connection]));
  const seen = new Set<string>();
  const ordered: ProviderConnection[] = [];
  for (const uid of fillOrder) {
    const connection = byId.get(uid);
    if (connection) {
      ordered.push(connection);
      seen.add(uid);
    }
  }
  for (const connection of connections) {
    if (!seen.has(connection.uid)) ordered.push(connection);
  }
  return ordered;
}

function swapAt(items: ProviderConnection[], index: number, delta: number) {
  const next = [...items];
  const other = index + delta;
  if (other < 0 || other >= next.length) return items;
  const current = next[index];
  next[index] = next[other];
  next[other] = current;
  return next;
}

export function PlacementSettingsCard({
  connections,
  isAdmin,
}: {
  connections: ProviderConnection[];
  isAdmin: boolean;
}) {
  const [placement, setPlacement] = useState<PlacementSettings | null>(null);

  useEffect(() => {
    api<PlacementSettings>("/settings/placement").then(setPlacement).catch((error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Could not load placement.");
    });
  }, []);

  async function save(changes: Partial<PlacementSettings>) {
    if (!placement) return;
    const previous = placement;
    const next = { ...placement, ...changes };
    setPlacement(next);
    try {
      const saved = await api<PlacementSettings>("/settings/placement", {
        method: "PATCH",
        body: JSON.stringify(changes),
      });
      setPlacement(saved);
    } catch (error) {
      setPlacement(previous);
      toast.error(error instanceof Error ? error.message : "Could not update placement.");
    }
  }

  const fillList = placement
    ? orderedConnections(connections, placement.fill_order)
    : connections;

  return (
    <Card>
      <CardHeader className="flex-row items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
          <HardDrive size={16} />
        </div>
        <div>
          <h2 className="font-semibold">Placement</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Each folder belongs to one storage. This policy only applies at the
            library root, or when a folder is not bound yet.
          </p>
        </div>
      </CardHeader>
      {placement && (
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="placement-policy">Policy</Label>
            {isAdmin ? (
              <Select
                onValueChange={(value) => {
                  if (typeof value === "string") {
                    if (value === "default" || value === "fill_order" || value === "most_free") {
                      void save({ policy: value });
                    }
                  }
                }}
                value={placement.policy}
              >
                <SelectTrigger className="w-full" id="placement-policy">
                  <SelectValue>
                    {(value: PlacementPolicy) => POLICY_LABELS[value]}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(POLICY_LABELS) as PlacementPolicy[]).map((policy) => (
                    <SelectItem key={policy} value={policy}>
                      {POLICY_LABELS[policy]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <p id="placement-policy" className="text-sm">
                {POLICY_LABELS[placement.policy]}
              </p>
            )}
            <p className="text-xs text-muted-foreground">{POLICY_HELP[placement.policy]}</p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="placement-default">Default storage</Label>
            {isAdmin && connections.length > 0 ? (
              <Select
                onValueChange={(value) => {
                  void save({
                    default_connection_id: value === AUTO_DEFAULT ? null : value,
                  });
                }}
                value={placement.default_connection_id ?? AUTO_DEFAULT}
              >
                <SelectTrigger className="w-full" id="placement-default">
                  <SelectValue>
                    {(value) =>
                      value === AUTO_DEFAULT
                        ? "First available"
                        : connections.find((connection) => connection.uid === value)?.name
                          ?? "Unknown storage"}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={AUTO_DEFAULT}>First available</SelectItem>
                  {connections.map((connection) => (
                    <SelectItem key={connection.uid} value={connection.uid}>
                      {connection.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <p id="placement-default" className="text-sm">
                {connections.find((item) => item.uid === placement.default_connection_id)?.name
                  ?? "First available"}
              </p>
            )}
          </div>

          {placement.policy !== "default" && fillList.length > 0 && (
            <div className="flex flex-col gap-2">
              <Label>Fill order</Label>
              <ol className="flex flex-col gap-2">
                {fillList.map((connection, index) => (
                  <li
                    className="flex items-center justify-between gap-3 rounded-lg border p-3"
                    key={connection.uid}
                  >
                    <span className="text-sm">
                      {index + 1}. {connection.name}
                    </span>
                    {isAdmin && (
                      <span className="flex gap-1">
                        <Button
                          aria-label={`Move ${connection.name} up`}
                          disabled={index === 0}
                          onClick={() =>
                            void save({
                              fill_order: swapAt(fillList, index, -1).map((item) => item.uid),
                            })
                          }
                          size="sm"
                          variant="outline"
                        >
                          <ChevronUp data-icon="inline-start" />
                        </Button>
                        <Button
                          aria-label={`Move ${connection.name} down`}
                          disabled={index === fillList.length - 1}
                          onClick={() =>
                            void save({
                              fill_order: swapAt(fillList, index, 1).map((item) => item.uid),
                            })
                          }
                          size="sm"
                          variant="outline"
                        >
                          <ChevronDown data-icon="inline-start" />
                        </Button>
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
