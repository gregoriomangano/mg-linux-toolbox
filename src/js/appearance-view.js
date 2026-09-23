// Pure, testable helpers for the global text-size control in Settings.
// The value is stored as a plain multiplier (1 = 100%) in localStorage,
// exactly like the existing theme preference (see setupTheme in main.js):
// no backend command, no privileged operation, nothing else on the page
// needs it. Applied purely through the `--ui-scale` CSS custom property
// consumed by every `font`/`font-size` declaration in the stylesheets --
// never `transform: scale()`, never a WebView zoom, so borders/icons stay
// crisp and the window resolution never changes.

export const STORAGE_KEY = "mg-text-scale";

// 90% .. 130% in fixed steps: fine enough to matter, coarse enough that
// every step has already been checked for layout regressions (see the
// session's manual verification pass).
export const TEXT_SCALE_OPTIONS = [0.9, 1.0, 1.1, 1.2, 1.3];

export const DEFAULT_TEXT_SCALE = 1;

// Snaps an arbitrary number to the nearest known option, and falls back to
// the default for anything not finite/positive (corrupted storage, a
// future downgrade that wrote an out-of-range value, manual tampering).
// Never lets an arbitrary value reach the CSS custom property unchecked.
export function clampTextScale(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return DEFAULT_TEXT_SCALE;
  let nearest = TEXT_SCALE_OPTIONS[0];
  let bestDelta = Math.abs(number - nearest);
  for (const option of TEXT_SCALE_OPTIONS) {
    const delta = Math.abs(number - option);
    if (delta < bestDelta) {
      nearest = option;
      bestDelta = delta;
    }
  }
  return nearest;
}

export function textScalePercentLabel(value) {
  return `${Math.round(clampTextScale(value) * 100)}%`;
}

export function loadTextScale(storage) {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (raw === null) return DEFAULT_TEXT_SCALE;
    return clampTextScale(raw);
  } catch {
    return DEFAULT_TEXT_SCALE;
  }
}

export function saveTextScale(storage, value) {
  const clamped = clampTextScale(value);
  try {
    storage.setItem(STORAGE_KEY, String(clamped));
  } catch {
    /* storage unavailable: the in-memory value still applies for this session */
  }
  return clamped;
}
