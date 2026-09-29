# Roadmap and limitations

UMedia is an early beta. The current implementation focuses on the unified library, provider connections, file operations, and a self-hosted deployment.

## Current focus

- Improve the setup and first-run experience.
- Verify cloud provider OAuth and storage operations with real accounts.
- Verify Telegram against a real channel and finish its interactive sign-in flow.
- Make the self-hosting path work for both local development and reverse-proxy deployments.
- Improve provider documentation and issue triage.

## Not implemented yet

- Full-text search and semantic indexing.
- A WebDAV server that exposes the library to Finder, Explorer, or a Linux mount.
- AI-agent APIs, automatic organization, or storage optimization.
- End-to-end validation for WebDAV, Nextcloud, FTP, and SFTP providers.

See the [GitHub issue tracker](https://github.com/mahdikiani/umedia/issues) for current work. Check the issue before starting a large change so effort is not duplicated.
