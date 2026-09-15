"use client";

import { Lock } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { AccessKeysSettings } from "@/components/access-keys-settings";
import { UsersSettings } from "@/components/users-settings";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError, type AuthState } from "@/lib/api";

export default function SettingsPage() {
  const [isAdmin, setIsAdmin] = useState(false);
  const [submittingPassword, setSubmittingPassword] = useState(false);

  useEffect(() => {
    api<AuthState>("/auth/state").then((authState) => {
      setIsAdmin(authState.user?.roles.includes("admin") ?? false);
    });
  }, []);

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const currentPassword = String(form.get("currentPassword"));
    const newPassword = String(form.get("newPassword"));
    const confirmPassword = String(form.get("confirmPassword"));
    if (newPassword !== confirmPassword) {
      toast.error("New passwords do not match.");
      return;
    }
    setSubmittingPassword(true);
    try {
      await api("/auth/password", {
        method: "PATCH",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      event.currentTarget.reset();
      toast.success("Password changed. You're still signed in on this device.");
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Password change failed.",
      );
    } finally {
      setSubmittingPassword(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Account</h1>
        <p className="text-sm text-muted-foreground">
          Password, users, and API access for this installation.
        </p>
      </div>

      {isAdmin && <UsersSettings />}

      <AccessKeysSettings />

      <Card>
        <CardHeader className="flex-row items-start gap-3">
          <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
            <Lock size={16} />
          </div>
          <div>
            <h2 className="font-semibold">Administrator password</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Changing it signs out every other session.
            </p>
          </div>
        </CardHeader>
        <CardContent>
          <form
            className="max-w-md space-y-4"
            onSubmit={(event) => void changePassword(event)}
          >
            {[
              ["Current password", "currentPassword"],
              ["New password", "newPassword"],
              ["Confirm new password", "confirmPassword"],
            ].map(([label, name]) => (
              <div className="space-y-1.5" key={name}>
                <Label htmlFor={name}>{label}</Label>
                <Input
                  id={name}
                  minLength={name === "currentPassword" ? 1 : 12}
                  name={name}
                  required
                  type="password"
                />
              </div>
            ))}
            <Button disabled={submittingPassword} type="submit">
              Change password
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
