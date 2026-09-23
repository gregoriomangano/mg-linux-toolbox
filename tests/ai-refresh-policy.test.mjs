import test from "node:test";
import assert from "node:assert/strict";
import {
  AI_DEFAULT_FRESHNESS_SECONDS,
  AI_MAX_FRESHNESS_SECONDS,
  aiFreshnessMs,
  aiNextRefreshDelayMs,
  aiSnapshotIsStale,
} from "../src/js/ai-refresh-policy.js";

const generatedAt = "2026-09-17T12:00:00.000Z";
const now = Date.parse(generatedAt);

test("AI freshness follows upstream staleAfterSeconds with safe bounds", () => {
  assert.equal(AI_DEFAULT_FRESHNESS_SECONDS, 180);
  assert.equal(AI_MAX_FRESHNESS_SECONDS, 300);
  assert.equal(aiFreshnessMs({ staleAfterSeconds: 180 }), 180_000);
  assert.equal(aiFreshnessMs({ staleAfterSeconds: 240 }), 240_000);
  assert.equal(aiFreshnessMs({ staleAfterSeconds: 900 }), 300_000);
  assert.equal(aiFreshnessMs({ staleAfterSeconds: 0 }), 180_000);
});

test("AI stale decisions use generatedAt rather than request time", () => {
  const snapshot = { generatedAt, staleAfterSeconds: 180, stale: false };
  assert.equal(aiSnapshotIsStale(snapshot, now + 30_000), false);
  assert.equal(aiSnapshotIsStale(snapshot, now + 179_999), false);
  assert.equal(aiSnapshotIsStale(snapshot, now + 180_000), true);
  assert.equal(aiNextRefreshDelayMs(snapshot, now + 30_000), 150_000);
});

test("failed cached snapshots retry normally without a tight polling loop", () => {
  const snapshot = { generatedAt, staleAfterSeconds: 180, stale: true };
  assert.equal(aiSnapshotIsStale(snapshot, now + 1), true);
  assert.equal(aiNextRefreshDelayMs(snapshot, now + 600_000), 180_000);
});

test("missing or invalid timestamps are stale but use the normal retry delay", () => {
  assert.equal(aiSnapshotIsStale(null, now), true);
  assert.equal(aiSnapshotIsStale({ generatedAt: "invalid" }, now), true);
  assert.equal(aiNextRefreshDelayMs({ generatedAt: "invalid" }, now), 180_000);
});
