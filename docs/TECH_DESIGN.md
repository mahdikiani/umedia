# Technical Design

> **Superseded** by the `00`–`09` doc series (start at
> [`02-architecture.md`](./02-architecture.md)). Kept for reference.


## High Level Architecture


                    Next.js

                       |

                    REST API

                       |

              Media Manager Core

                       |

        --------------------------------

        Metadata     Search     Sharing

                       |

              Storage Provider Layer


        Local
        S3
        Google Drive
        Telegram
        FTP
        SFTP



## Frontend

Framework:

Next.js

Language:

TypeScript

UI:

shadcn/ui

Styling:

TailwindCSS


## Backend

Framework:

FastAPI


Database:

SQLite


ORM:

SQLAlchemy


Architecture:

- Repository layer
- Service layer
- API layer


## Core Modules


### Media Manager

Responsible for:

- Managing MediaFile
- Resolving StorageObject
- File operations
- Sharing


### Provider Manager

Responsible for:

- Provider registration
- Connection management
- Provider lifecycle


### Search Manager

Responsible for:

- Indexing
- Searching
- Updating indexes


### Worker

Responsible for:

- Background synchronization
- Metadata indexing
- Long running operations



## Storage Flow


Upload:

Client

↓

API

↓

Media Manager

↓

Storage Provider

↓

Create MediaFile + StorageObject



Download:

Client

↓

Media Manager

↓

Resolve StorageObject

↓

Provider

↓

Stream Response



