"""Connection names: display labels that are also S3 bucket names.

Each connection is exposed as its own bucket in the public S3-compatible
API (`apps/s3`), so its name follows AWS general-purpose bucket naming
rules (https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html)
with one stricter rule: no dots. A dot would put the bucket on a deeper
DNS level in virtual-host addressing (`{bucket}.{endpoint}`), which the
wildcard certificate and `vhost.bucket_from_hostname` do not cover.
"""

import re

from server.config import Settings

MIN_LENGTH = 3
MAX_LENGTH = 63
_ALLOWED = re.compile(r"^[a-z0-9-]+$")
_FORBIDDEN_PREFIXES = ("xn--", "sthree-", "amzn-s3-demo-")
_FORBIDDEN_SUFFIXES = ("-s3alias", "--ol-s3", "--x-s3", "--table-s3")
# The whole-library bucket, plus names that would collide with routes the
# S3 root router or the web app serve at the top level.
RESERVED_NAMES = frozenset({Settings.S3_COMPAT_BUCKET, "api", "s3", "www"})
_FALLBACK = "storage"


class InvalidConnectionName(ValueError):  # noqa: N818 -- domain name, wrapped by InvalidConnectionNameError
    """The name cannot be used as an S3 bucket name."""


def validate_connection_name(name: str) -> str:
    """Return `name` unchanged when it is a valid bucket name, else raise
    `InvalidConnectionName` with the rule it breaks."""
    if not MIN_LENGTH <= len(name) <= MAX_LENGTH:
        raise InvalidConnectionName(
            f"Name must be between {MIN_LENGTH} and {MAX_LENGTH} characters",
        )
    if not _ALLOWED.match(name):
        raise InvalidConnectionName(
            "Name may contain only lowercase letters, numbers, and hyphens",
        )
    if name[0] == "-" or name[-1] == "-":
        raise InvalidConnectionName("Name must start and end with a letter or number")
    # Before the `--` rule, so `xn--...` gets the more specific message.
    for prefix in _FORBIDDEN_PREFIXES:
        if name.startswith(prefix):
            raise InvalidConnectionName(f"Name cannot start with '{prefix}'")
    if "--" in name:
        raise InvalidConnectionName("Name cannot contain consecutive hyphens")
    for suffix in _FORBIDDEN_SUFFIXES:
        if name.endswith(suffix):
            raise InvalidConnectionName(f"Name cannot end with '{suffix}'")
    if name in RESERVED_NAMES:
        raise InvalidConnectionName(f"'{name}' is a reserved name")
    return name


def slugify_connection_name(raw: str) -> str:
    """Best-effort conversion of any label into a valid name, e.g.
    `"Microsoft OneDrive"` -> `"microsoft-onedrive"`. Used to migrate
    existing free-text names and to suggest a default in the UI."""
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    for prefix in _FORBIDDEN_PREFIXES:
        slug = slug.removeprefix(prefix)
    slug = slug[:MAX_LENGTH].strip("-")
    for suffix in _FORBIDDEN_SUFFIXES:
        slug = slug.removesuffix(suffix)
    if not slug:
        return _FALLBACK
    if len(slug) < MIN_LENGTH or slug in RESERVED_NAMES:
        slug = f"{slug}-{_FALLBACK}"[:MAX_LENGTH]
    return slug


def unique_connection_name(name: str, taken: set[str]) -> str:
    """`name`, or `name-2`, `name-3`, ... -- trimmed so the result still
    fits in 63 characters -- whichever is not in `taken`."""
    if name not in taken:
        return name
    counter = 2
    while True:
        suffix = f"-{counter}"
        candidate = f"{name[: MAX_LENGTH - len(suffix)].rstrip('-')}{suffix}"
        if candidate not in taken:
            return candidate
        counter += 1


# Endpoint host fragments -> brand, for S3-compatible connections. Only
# the brand is derived and returned; the endpoint itself stays encrypted.
_S3_HOSTS = (
    ("r2.cloudflarestorage.com", "cloudflare"),
    ("backblazeb2.com", "backblaze"),
    ("wasabisys.com", "wasabi"),
    ("digitaloceanspaces.com", "digitalocean"),
    ("amazonaws.com", "aws"),
)
_S3_PROFILES = {"minio": "minio", "cloudflare": "cloudflare", "aws": "aws"}
_WEBDAV_VENDORS = {"nextcloud", "owncloud"}


def connection_variant(provider_type: str, config: dict) -> str | None:
    """Which service an S3 or WebDAV connection points at (for its logo),
    or `None` when there is nothing more specific than the protocol."""
    if provider_type == "s3":
        endpoint = str(config.get("endpoint_url") or "").lower()
        for fragment, brand in _S3_HOSTS:
            if fragment in endpoint:
                return brand
        profile = str(config.get("provider") or "").strip().lower()
        if profile in _S3_PROFILES:
            return _S3_PROFILES[profile]
        return None if endpoint else "aws"
    if provider_type == "webdav":
        vendor = str(config.get("vendor") or "").strip().lower()
        return vendor if vendor in _WEBDAV_VENDORS else None
    return None
