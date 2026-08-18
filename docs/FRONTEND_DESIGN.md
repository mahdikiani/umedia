# Frontend Design

> **Superseded** by the `00`–`09` doc series (start at
> [`08-implementation-plan.md`](./08-implementation-plan.md)). Kept for
> reference; the frontend phase is backlog for now (see
> [`09-tasks.md`](./09-tasks.md#backlog-explicitly-deferred-not-dropped))
> and will be rebased on `next-shadcn-admin-dashboard` rather than built
> from scratch as sketched below.


## Framework

Next.js


## Language

TypeScript


## UI

shadcn/ui


## Styling

TailwindCSS


## State Management

TanStack Query


## Pages


/dashboard

Overview of storage providers and usage.



/providers

Manage provider connections.



/files

File explorer.



/search

Global search.



/shares

Manage public links.



/settings



## Components


ProviderCard

StorageUsageCard

FileExplorer

FileTable

SearchBox

ShareDialog

UploadDialog



## Rules


Frontend must not access database directly.

All data comes from REST API.

Keep components reusable.

Use TypeScript strictly.

