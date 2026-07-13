import React from "react";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { VideoEditorView } from "./VideoEditorView.tsx";

describe("VideoEditorView shell", () => {
  it("renders media workspace without embedded agent chat strip", () => {
    const html = renderToStaticMarkup(
      <VideoEditorView apiBase="http://localhost:8000" sessionId="" projectId="" />,
    );
    expect(html.includes("video-editor-shell")).toEqual(true);
    expect(html.includes("video-media-bin")).toEqual(true);
    expect(html.includes("video-program-viewer")).toEqual(true);
    expect(html.includes("video-timeline")).toEqual(true);
    expect(html.includes("video-agent-strip")).toEqual(false);
    expect(html.includes("Tell Echo what to do")).toEqual(false);
    expect(html.includes("Direct Echo")).toEqual(false);
    expect(html.includes("No media yet — Import video, audio, or images.")).toEqual(true);
    expect(html.includes("Project-relative media path")).toEqual(false);
  });

  it("keeps V1/A1, media toggle, and disabled Generate/Export", () => {
    const html = renderToStaticMarkup(
      <VideoEditorView apiBase="http://localhost:8000" sessionId="s1" projectId="p1" projectName="Demo" />,
    );
    expect(html.includes(">V1<")).toEqual(true);
    expect(html.includes(">A1<")).toEqual(true);
    expect(html.includes("video-media-toggle")).toEqual(true);
    expect(html.includes("Hide media") || html.includes("Show media")).toEqual(true);
    expect(html.includes("Import")).toEqual(true);
    expect(html.includes("Generate")).toEqual(true);
    expect(html.includes("Export")).toEqual(true);
  });
});
