"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  CONNECTION_NAME_PATTERN,
  connectionNameError,
  suggestConnectionName,
} from "@/lib/connection-name";

/** The "Connection name" input shared by every connect flow. The name is
 * also the connection's S3 bucket, so it starts as a valid suggestion
 * derived from the provider name and shows the bucket rules as the user
 * types. The native `pattern` blocks submit on an invalid name; the
 * server re-checks and also rejects names the user already has. */
export function ConnectionNameField({
  providerName,
  existingNames = [],
}: {
  providerName: string;
  existingNames?: string[];
}) {
  const [value, setValue] = useState(() =>
    suggestConnectionName(providerName, existingNames),
  );
  const ruleError = connectionNameError(value);
  const duplicate = existingNames.includes(value)
    ? "You already have a connection with this name"
    : null;
  const error = ruleError ?? duplicate;

  return (
    <div className="space-y-1.5 sm:col-span-2">
      <Label htmlFor="connection_name">Connection name</Label>
      <Input
        aria-describedby="connection_name_hint"
        aria-invalid={error ? true : undefined}
        autoCapitalize="none"
        autoComplete="off"
        className="font-mono"
        id="connection_name"
        maxLength={63}
        minLength={1}
        name="connection_name"
        onChange={(event) => setValue(event.target.value.toLowerCase())}
        pattern={CONNECTION_NAME_PATTERN}
        required
        spellCheck={false}
        value={value}
      />
      <p
        aria-live="polite"
        className={error ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
        id="connection_name_hint"
      >
        {error ??
          "Also its S3 bucket name: 1–63 lowercase letters, numbers, and hyphens."}
      </p>
    </div>
  );
}
