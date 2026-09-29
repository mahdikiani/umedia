# UMedia public launch plan

## Positioning

Introduce UMedia as an early-beta, self-hosted media library for people who want to manage files from multiple storage providers in one place. Lead with ownership and the unified library; be explicit that some provider integrations need real-account verification. Do not describe UMedia as a general filesystem, cloud service, or AI product.

## Before the first announcement

1. Finish the README and public MkDocs site; verify every installation step against the checked-in Compose setup.
2. Review the public branch, Git history, secrets, sample configuration, license, dependency licenses, and CI. Keep local `.env` files, runtime data, and build output out of Git.
3. Make the repository public after that review, confirm Issues remain enabled, configure Pages to deploy from GitHub Actions, and check the published pages without being signed in.
4. Add a release note with the tested commit and known limitations. Do not tag a stable release while real provider operations remain unverified.
5. Prepare three or more current screenshots, a short setup demo, and a maker comment that explains why UMedia exists and what feedback is most useful.

## Suggested sequence

### Week 1: open source and feedback

- Publish the repository and documentation site.
- Announce the project on Hacker News with a `Show HN` post focused on the technical approach and early-beta status. Answer comments from the maintainer account.
- Share one tailored post in an open-source or self-hosting community only after checking that community's current self-promotion rules. Avoid cross-posting identical promotional copy.

### Week 2: Product Hunt

- Submit UMedia as the maker. A third-party hunter is not required according to Product Hunt's current launch guide.
- Add a useful maker comment, clear screenshots, and a concise demo. Ask people to visit and comment with feedback; do not ask directly for upvotes.
- Share the Product Hunt page only where community rules allow and respond to questions during the day.

### Ongoing: earn trust

- Answer GitHub Issues and update docs when a report reveals a setup gap.
- Post meaningful release notes when capabilities change; avoid repeated launch posts.
- Track GitHub stars as a reach signal, but prioritize completed self-hosting attempts, reproducible provider reports, useful issues, and contributor activity. Do not add analytics to the app for this launch.

## Support operating routine

- GitHub Issues is the main support queue. Review new issues several times during launch week, label bugs/provider questions, ask for a minimal reproduction, and confirm a fix or next step.
- Email and Telegram are direct-contact fallbacks. When a technical issue arrives there, ask permission to move a sanitized summary to GitHub so it can be tracked publicly.
- Never request or repost tokens, OAuth secrets, Telegram sessions, private files, or unredacted logs.
- There is no response-time SLA. Say so in the docs rather than promising coverage the maintainer cannot guarantee.

## Promotion checklist

- [ ] Public repository and Pages URL work in a logged-out browser.
- [ ] README and provider matrix match the release commit.
- [ ] CI and documentation build pass.
- [ ] Screenshots show the current product, not mockups.
- [ ] Each selected subreddit/community allows the post under its current rules.
- [ ] The Product Hunt post does not request votes.
- [ ] Support channels and known limitations are visible.
- [ ] No donation address or Sponsors link is invented; add a real destination once configured.
