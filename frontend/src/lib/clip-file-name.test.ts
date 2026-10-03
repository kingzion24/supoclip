import { describe, expect, it } from "vitest";
import { clipFileName } from "./clip-actions";

describe("clipFileName", () => {
  it("numbers clips and uses a safe version of the hook title", () => {
    expect(clipFileName(3, "Siri ya mafanikio: 500$ kwa wiki?", "clip_3_x.mp4")).toBe(
      "03-Siri-ya-mafanikio-500-kwa-wiki.mp4",
    );
  });

  it("falls back to the stored file name and adds the export suffix", () => {
    expect(clipFileName(12, null, "clip_12_0100-0130_ab.mp4", "_tiktok")).toBe(
      "12-clip_12_0100-0130_ab_tiktok.mp4",
    );
  });
});
