# UMedia Design System

## 1. Design principles

- Prioritize clear media-management workflows over decoration.
- Keep controls compact, predictable, and close to the content they affect.
- Reuse shadcn/Base UI primitives and semantic tokens instead of page-specific
  visual rules.
- Preserve useful density while allowing toolbars and tables to adapt on narrow
  screens.

## 2. Color system

The application uses the neutral OKLCH token set in `apps/web/app/globals.css`.
Surfaces use `background`, `card`, and `popover`; text uses `foreground` and
`muted-foreground`; interaction states use `primary`, `accent`, `destructive`,
`border`, `input`, and `ring`. Components must use these semantic tokens so the
existing light and dark themes remain equivalent.

## 3. Typography

English interfaces use Geist through the shared `font-sans` token. Persian
interfaces remap that token to Vazirmatn. Body and control text is normally
`text-sm`, compact controls may use the shared `text-[0.8rem]` small size, and
headings use the same family with weight for hierarchy. Labels stay concise and
sentence-cased.

## 4. Spacing and layout

Use the existing Tailwind spacing scale, with `gap-2` for compact action groups,
`gap-3` between toolbar regions, and `space-y-4` between page sections. Dashboard
toolbars use wrapping flex layouts so controls remain usable without horizontal
overflow. Primary content tables sit in a `rounded-xl border` container.
The dual-pane Files view follows StyleGallery's
[`split-screen`](https://github.com/changeroa/StyleGallery/blob/main/patterns/split-sidebar/split-screen.md)
pattern: equal
tracks use `minmax(0, 1fr)` at the wide layout breakpoint and stack when space
is tight. Each pane may shrink to its grid track; the document keeps vertical
scroll ownership, and the file table never introduces horizontal scrolling.
The five table columns use a fixed layout with bounded metadata columns. The
name cell truncates long names and exposes the full value on hover.

## 5. Component patterns

- Use shared components from `apps/web/components/ui`.
- Toolbar buttons and selects use the small size and the existing rounded border
  treatment.
- Select menus use the shared popover surface, check indicator, focus highlight,
  and restrained opening motion.
- Long storage-setup dialogs use a viewport-bounded shell: the title stays in
  place, the form body owns vertical scrolling, and its action row stays pinned
  to the bottom of that scroll area. The body must be allowed to shrink with
  `min-h-0`; the dialog width expands to `sm:max-w-2xl` for provider OAuth.
- Mutations refresh the current browse context; view controls update URL state so
  browser history and deep links remain meaningful.
- File browser lists preserve all five columns inside a pane. Long filenames
  truncate in the name column while retaining their full value in the hover
  title; tables do not scroll horizontally.

## 6. Interaction states

Every interactive control must expose a visible keyboard focus ring through the
shared `ring` token. Hover and expanded states use `muted` or `accent`; disabled
states reduce opacity and block pointer interaction. Loading operations retain
their current spinner, progress, or disabled affordance. URL-controlled selects
must render the active option on first load.

## 7. Border and elevation policy

Use borders to group persistent page content and inputs. Reserve elevation for
temporary layers such as select popovers, menus, dialogs, and toasts. Persistent
toolbar controls use borders without shadows; popovers use the shared medium
shadow and subtle foreground ring.

## 8. Accessibility and responsive behavior

Controls need accessible names independent of visible icons, full keyboard
operation, and semantic component primitives. Preserve readable contrast through
semantic tokens in both themes. At narrow widths, toolbar groups wrap rather than
shrink below usable sizes; labels may remain visible because the Files actions are
short and disambiguate adjacent controls. Keep logical-direction utilities so LTR
and RTL layouts both work.
