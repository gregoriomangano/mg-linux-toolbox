export const AI_DEFAULT_FRESHNESS_SECONDS = 180;
export const AI_MAX_FRESHNESS_SECONDS = 300;
const AI_MIN_FRESHNESS_SECONDS = 60;

export function aiFreshnessMs(snapshot) {
  const upstream = Number(snapshot?.staleAfterSeconds);
  const seconds = Number.isFinite(upstream) && upstream > 0
    ? Math.min(AI_MAX_FRESHNESS_SECONDS, Math.max(AI_MIN_FRESHNESS_SECONDS, upstream))
    : AI_DEFAULT_FRESHNESS_SECONDS;
  return seconds * 1000;
}

export function aiSnapshotAgeMs(snapshot, now = Date.now()) {
  const generatedAt = Date.parse(snapshot?.generatedAt || "");
  return Number.isFinite(generatedAt) ? Math.max(0, now - generatedAt) : null;
}

export function aiSnapshotIsStale(snapshot, now = Date.now()) {
  if (!snapshot || snapshot.stale) return true;
  const age = aiSnapshotAgeMs(snapshot, now);
  return age == null || age >= aiFreshnessMs(snapshot);
}

export function aiNextRefreshDelayMs(snapshot, now = Date.now()) {
  const freshness = aiFreshnessMs(snapshot);
  const age = aiSnapshotAgeMs(snapshot, now);
  if (snapshot?.stale || age == null) return freshness;
  return Math.max(0, freshness - age);
}
