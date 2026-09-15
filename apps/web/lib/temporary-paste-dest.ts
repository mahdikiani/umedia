/**
 * Resolve where Temporary dock Paste (move/copy) should land.
 *
 * Dual-pane: the user picks left (primary) vs right (secondary).
 * Single-pane: always the folder currently browsed in the primary pane.
 */

export type TemporaryPastePane = "primary" | "secondary";

export type TemporaryPasteDestInput = {
  dualPane: boolean;
  /** User selection when dual-pane is on. Ignored when dualPane is false. */
  selectedPane: TemporaryPastePane;
  primaryParentId: string | null;
  secondaryParentId: string | null;
};

/**
 * TODO (you): return the dest_parent_id for Temporary paste.
 *
 * Rules:
 * - If dualPane is false → always primaryParentId
 * - If dualPane is true and selectedPane is "primary" → primaryParentId
 * - If dualPane is true and selectedPane is "secondary" → secondaryParentId
 */
export function resolveTemporaryPasteDest(
  input: TemporaryPasteDestInput,
): string | null {
  // Implement the three rules above (about 5 lines).
  throw new Error("resolveTemporaryPasteDest not implemented");
}

/**
 * True when a move would leave every source in the same parent it already has.
 * Used to skip no-op same-folder moves (UI + DnD + Temporary paste).
 */
export function isSameFolderMove(
  sourceParentIds: Array<string | null | undefined>,
  destParentId: string | null,
): boolean {
  if (sourceParentIds.length === 0) return false;
  return sourceParentIds.every((parentId) => (parentId ?? null) === destParentId);
}
