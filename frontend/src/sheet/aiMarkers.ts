import type { AiChange } from "../api/types";

export function latestAiFieldChanges(changes: AiChange[]): Map<string, Map<string, AiChange>> {
  const latest = new Map<string, Map<string, AiChange>>();
  for (const change of changes) {
    if (change.undone_at !== null || change.application_id === null) continue;
    let fields = latest.get(change.application_id);
    if (!fields) {
      fields = new Map();
      latest.set(change.application_id, fields);
    }
    for (const field of Object.keys(change.changes)) {
      const previous = fields.get(field);
      if (!previous || change.received_at > previous.received_at) fields.set(field, change);
    }
  }
  return latest;
}

export function aiChangeError(error: unknown): string {
  if (error instanceof Error) {
    if (error.message === "changed_since") return "This cell changed after the AI set it.";
    if (error.message === "already_undone") return "Already undone.";
    return error.message;
  }
  return "Could not update the AI change.";
}
