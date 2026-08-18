"use client";

import { Plus, Users } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { useLocale } from "@/components/locale-provider";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, ApiError, type UserSummary } from "@/lib/api";
import { useCopy } from "@/lib/copy";

type UserRole = "admin" | "user";

function primaryRole(user: UserSummary): UserRole {
  return user.roles.includes("admin") ? "admin" : "user";
}

export function UsersSettings() {
  const { locale } = useLocale();
  const text = useCopy(locale);
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [createRole, setCreateRole] = useState<UserRole>("user");
  const [creating, setCreating] = useState(false);
  const [updatingUid, setUpdatingUid] = useState<string | null>(null);
  const [deletingUid, setDeletingUid] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api<UserSummary[]>("/users")
      .then((list) => {
        if (!cancelled) setUsers(list);
      })
      .catch((error) => {
        if (!cancelled) {
          toast.error(error instanceof ApiError ? error.message : text.usersLoadFailed);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [text.usersLoadFailed]);

  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const name = String(form.get("name") ?? "").trim();
    setCreating(true);
    try {
      const created = await api<UserSummary>("/users", {
        method: "POST",
        body: JSON.stringify({
          email: String(form.get("email")),
          password: String(form.get("password")),
          role: createRole,
          name: name || null,
        }),
      });
      setUsers((previous) => [...previous, created]);
      formElement.reset();
      setCreateRole("user");
      setAddOpen(false);
      toast.success(text.userCreated);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : text.userCreateFailed);
    } finally {
      setCreating(false);
    }
  }

  async function updateUser(
    user: UserSummary,
    changes: { role?: UserRole; is_active?: boolean },
  ) {
    setUpdatingUid(user.uid);
    try {
      const updated = await api<UserSummary>(`/users/${user.uid}`, {
        method: "PATCH",
        body: JSON.stringify(changes),
      });
      setUsers((previous) =>
        previous.map((item) => (item.uid === updated.uid ? updated : item)),
      );
      toast.success(text.userUpdated);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : text.userUpdateFailed);
    } finally {
      setUpdatingUid(null);
    }
  }

  async function deleteUser(user: UserSummary) {
    setDeletingUid(user.uid);
    try {
      await api(`/users/${user.uid}`, { method: "DELETE" });
      setUsers((previous) => previous.filter((item) => item.uid !== user.uid));
      toast.success(text.userDeleted);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : text.userDeleteFailed);
    } finally {
      setDeletingUid(null);
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="flex gap-3">
          <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
            <Users size={16} />
          </div>
          <div>
            <h2 className="font-semibold">{text.usersTitle}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{text.usersBody}</p>
          </div>
        </div>

        <Dialog onOpenChange={setAddOpen} open={addOpen}>
          <DialogTrigger render={<Button size="sm" type="button" />}>
            <Plus size={14} /> {text.addUser}
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{text.addUser}</DialogTitle>
              <DialogDescription>{text.usersBody}</DialogDescription>
            </DialogHeader>
            <form id="create-user-form" className="space-y-4" onSubmit={createUser}>
              <div className="space-y-1.5">
                <Label htmlFor="user-name">{text.name}</Label>
                <Input
                  autoComplete="name"
                  id="user-name"
                  maxLength={255}
                  name="name"
                  placeholder={text.optionalName}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="user-email">{text.email}</Label>
                <Input
                  autoComplete="email"
                  id="user-email"
                  name="email"
                  required
                  type="email"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="user-password">{text.password}</Label>
                <Input
                  aria-describedby="user-password-help"
                  autoComplete="new-password"
                  id="user-password"
                  minLength={12}
                  name="password"
                  required
                  type="password"
                />
                <p id="user-password-help" className="text-xs text-muted-foreground">
                  {text.passwordMinimum}
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="user-role">{text.role}</Label>
                <Select
                  onValueChange={(value) => {
                    if (value === "admin" || value === "user") setCreateRole(value);
                  }}
                  value={createRole}
                >
                  <SelectTrigger className="w-full" id="user-role">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="user">{text.roleUser}</SelectItem>
                    <SelectItem value="admin">{text.roleAdmin}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </form>
            <DialogFooter>
              <Button
                disabled={creating}
                form="create-user-form"
                type="submit"
              >
                {creating ? text.creatingUser : text.createUser}
              </Button>
              <Button onClick={() => setAddOpen(false)} type="button" variant="outline">
                {text.cancel}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardHeader>

      <CardContent className="space-y-2">
        {loading && (
          <p className="text-sm text-muted-foreground" role="status">
            {text.usersLoading}
          </p>
        )}
        {!loading && users.length === 0 && (
          <p className="text-sm text-muted-foreground">{text.noUsers}</p>
        )}
        {users.map((user) => {
          const role = primaryRole(user);
          const pending = updatingUid === user.uid || deletingUid === user.uid;
          return (
            <div
              className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
              key={user.uid}
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{user.email}</div>
                {user.name && (
                  <div className="truncate text-xs text-muted-foreground">{user.name}</div>
                )}
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge variant="secondary">
                    {role === "admin" ? text.roleAdmin : text.roleUser}
                  </Badge>
                  <Badge variant={user.is_active ? "outline" : "destructive"}>
                    {user.is_active ? text.active : text.inactive}
                  </Badge>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                <Select
                  disabled={pending}
                  onValueChange={(value) => {
                    if ((value === "admin" || value === "user") && value !== role) {
                      void updateUser(user, { role: value });
                    }
                  }}
                  value={role}
                >
                  <SelectTrigger
                    aria-label={`${text.changeRole}: ${user.email}`}
                    className="w-28"
                    size="sm"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="user">{text.roleUser}</SelectItem>
                    <SelectItem value="admin">{text.roleAdmin}</SelectItem>
                  </SelectContent>
                </Select>
                <Button
                  aria-label={user.is_active ? text.deactivateUser : text.activateUser}
                  disabled={pending}
                  onClick={() => void updateUser(user, { is_active: !user.is_active })}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  {user.is_active ? text.deactivateUser : text.activateUser}
                </Button>
                <AlertDialog>
                  <AlertDialogTrigger
                    render={
                      <Button disabled={pending} size="sm" type="button" variant="destructive" />
                    }
                  >
                    {text.deleteUser}
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>{text.deleteUserTitle}</AlertDialogTitle>
                      <AlertDialogDescription>
                        {text.deleteUserBody} <strong>{user.email}</strong>
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>{text.cancel}</AlertDialogCancel>
                      <AlertDialogAction
                        disabled={deletingUid === user.uid}
                        onClick={() => void deleteUser(user)}
                        variant="destructive"
                      >
                        {text.deleteUser}
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
