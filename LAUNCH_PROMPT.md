# Prompt for GPT to introduce UMedia

Copy the prompt below into GPT in an environment that can read this local repository and operate a browser with your already-signed-in accounts.

---

You are helping me publish an accurate public introduction to my open-source project, UMedia.

## Project and sources of truth

- Local repository: `/home/mahdi/Projects/umedia`
- Public repository: `https://github.com/mahdikiani/umedia`
- Documentation: `https://mahdikiani.github.io/umedia/`
- Existing maintainer preview: `https://umedia.uln.me` (this is not a public signup service)
- License: MIT
- Maintainer: Mahdi Kiani
- Contact: `mahdikiany@gmail.com`, Telegram `@mahdikiani`, GitHub `@mahdikiani`
- Main support channel: GitHub Issues at `https://github.com/mahdikiani/umedia/issues`
- PyPI profile: `https://pypi.org/user/mahdikiani/`. `usso` and `fastapi-mongo-base` are separate maintainer projects, not UMedia features.

Before writing anything, read the current root README, `docs/public/`, `LAUNCH_PLAN.md`, and the relevant implementation/docs for every feature you mention. Treat the local source and published documentation as the only authority for product claims. Check that the repository and documentation URLs are public and currently load; if they are not, stop promotion and report the exact blocker.

## Accurate product description

Describe UMedia as an early-beta, self-hosted media library that brings files from configured storage providers into one web interface. It uses Docker Compose and SQLite. Provider states differ: local filesystem and S3-compatible storage are advertised as available, while Google Drive, OneDrive, Dropbox, and Telegram are beta. The provider page lists important verification gaps. Do not claim that every provider, operation, or deployment has been verified.

Do not claim UMedia has full-text/semantic search, a WebDAV mount server, AI organization, public signup, or a stable production release. Do not confuse the maintainer's PyPI packages with UMedia itself. The current Compose setup expects an existing Traefik network and routing; do not promise one-command standalone installation.

## Task

1. Inspect the current rules and posting requirements on Product Hunt, Hacker News, and any relevant Reddit/community you consider. Use only communities where this post is allowed; do not evade filters or moderator rules.
2. Prepare platform-specific copy in English. Be candid, useful, and written in a solo-maintainer voice. Explain the problem, what works today, what remains beta, and exactly what feedback would help.
3. Submit the maker launch to Product Hunt if its current rules and my signed-in account allow it. A third-party hunter is unnecessary. Do not ask anyone directly to upvote; invite visits and substantive feedback instead.
4. Publish a `Show HN` post if the current guidelines allow it, then publish at most one tailored Reddit/community post where the current rules permit self-promotion. Do not paste identical promotional text across communities.
5. Link GitHub Issues as the primary technical support route. Include my email and Telegram only as secondary contact options. Do not promise a response-time SLA.
6. Do not invent a donation link. GitHub Sponsors or a dedicated donation wallet may be added later, but no destination is configured yet.
7. After posting, monitor replies during this session. Answer only questions supported by the repository/docs. If a question is uncertain, say you will verify it rather than guessing. Never expose local paths, credentials, private deployment configuration, user data, or environment-variable values.

I authorize you to publish the project announcement and respond to public comments under the signed-in accounts I choose to use, within the scope above. Do not change repository visibility, account settings, billing, donation settings, or other profile settings. Do not send private messages. If a platform requires an action outside this scope or asks for a claim you cannot verify, skip that action and report why.

At the end, report each platform, the public URL, publication time, and any replies that need my attention. If a post was not published, say why; do not claim success without a visible post URL.
