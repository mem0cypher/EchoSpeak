/** Persist Video Editor panel sizes (local only; not Session state). */

export type VideoEditorPanelLayout = {
  leftWidth: number;
  rightWidth: number;
  timelineHeight: number;
  /** When false, media bin collapses so the viewer expands horizontally. */
  mediaBinVisible: boolean;
};

export const defaultVideoEditorLayout: VideoEditorPanelLayout = {
  leftWidth: 268,
  rightWidth: 300,
  timelineHeight: 248,
  mediaBinVisible: true,
};

const STORAGE_KEY = "echospeak.video_editor.panels";

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

export function loadVideoEditorLayout(storage: Pick<Storage, "getItem"> | null): VideoEditorPanelLayout {
  if (!storage) return { ...defaultVideoEditorLayout };
  try {
    const raw = JSON.parse(storage.getItem(STORAGE_KEY) || "{}");
    return {
      leftWidth: clamp(Number(raw.leftWidth) || defaultVideoEditorLayout.leftWidth, 200, 420),
      rightWidth: clamp(Number(raw.rightWidth) || defaultVideoEditorLayout.rightWidth, 220, 440),
      timelineHeight: clamp(Number(raw.timelineHeight) || defaultVideoEditorLayout.timelineHeight, 160, 480),
      mediaBinVisible: raw.mediaBinVisible !== false,
    };
  } catch {
    return { ...defaultVideoEditorLayout };
  }
}

export function saveVideoEditorLayout(
  storage: Pick<Storage, "setItem"> | null,
  layout: VideoEditorPanelLayout,
): void {
  if (!storage) return;
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      leftWidth: clamp(layout.leftWidth, 200, 420),
      rightWidth: clamp(layout.rightWidth, 220, 440),
      timelineHeight: clamp(layout.timelineHeight, 160, 480),
      mediaBinVisible: layout.mediaBinVisible !== false,
    }),
  );
}

export function formatTimecode(seconds: number): string {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}
