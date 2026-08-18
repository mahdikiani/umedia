"""Per-user S3-style access key pairs.

Each user owns key pairs: a public `access_key_id` (safe to carry in
URLs / SigV4 Credential) and a secret that never leaves the server --
stored only as a Fernet token under the installation `CredentialCipher`.
The S3-compatible gateway (`apps/s3`) authenticates with these keys, and
temporary share links are standard SigV4-presigned GETs signed with the
minting user's secret -- so a link dies with that key's deactivation.
"""
