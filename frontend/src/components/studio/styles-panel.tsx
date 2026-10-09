"use client";

import { useState } from "react";
import { Loader2, Plus, RefreshCw, Sparkles, Trash2, Youtube } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { readError, type StudioStyle } from "@/lib/studio";

const STATUS: Record<StudioStyle["status"], string> = {
  queued: "Waiting…",
  reading: "Reading the videos…",
  writing: "Writing the style guide…",
  ready: "Ready",
  error: "Failed",
};

export function StylesPanel({ styles, reload }: { styles: StudioStyle[]; reload: () => Promise<void> }) {
  const [name, setName] = useState("");
  const [urls, setUrls] = useState("");
  const [saving, setSaving] = useState(false);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      const list = urls.split(/[\s,]+/).map((url) => url.trim()).filter(Boolean);
      const response = await fetch("/api/studio/styles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, urls: list }),
      });
      if (!response.ok) throw new Error(await readError(response, "Could not add the inspiration"));
      setName("");
      setUrls("");
      await reload();
      toast.success("Katakata is studying those videos. This takes a minute or two.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not add the inspiration");
    } finally {
      setSaving(false);
    }
  }

  async function action(path: string, method: string) {
    const response = await fetch(`/api/studio/styles/${path}`, { method });
    if (!response.ok) toast.error(await readError(response, "That did not work"));
    await reload();
  }

  return (
    <section className="rounded-2xl border bg-card p-4 sm:p-5" aria-labelledby="styles-heading">
      <h2 id="styles-heading" className="flex items-center gap-2 text-sm font-semibold">
        <Sparkles className="size-4" />Inspiration channels
      </h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Paste YouTube channels or videos whose storytelling you admire. Katakata reads their titles and subtitles
        and writes a style guide (hooks, pacing, narration, visuals). Your scripts learn the techniques, never their words or stories.
      </p>
      <form onSubmit={add} className="mt-3 grid gap-2 sm:grid-cols-[200px_1fr_auto]">
        <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Name, e.g. Documentary style" maxLength={80} />
        <Textarea
          value={urls} onChange={(event) => setUrls(event.target.value)} rows={1}
          placeholder="https://www.youtube.com/@channel  https://youtu.be/…  (up to 6 links)"
        />
        <Button type="submit" disabled={saving || name.trim().length < 2 || !urls.trim()}>
          {saving ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}Add
        </Button>
      </form>
      {styles.length > 0 && (
        <ul className="mt-4 space-y-2">
          {styles.map((style) => (
            <li key={style.id} className="rounded-xl border bg-background p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{style.name}</span>
                <span className={style.status === "error" ? "text-xs text-red-700" : "text-xs text-muted-foreground"}>
                  {style.status !== "ready" && style.status !== "error" && <Loader2 className="mr-1 inline size-3 animate-spin" />}
                  {STATUS[style.status]}
                </span>
                <div className="ml-auto flex gap-1">
                  <Button size="icon" variant="ghost" className="size-7" aria-label="Study again" onClick={() => void action(`${style.id}/refresh`, "POST")}>
                    <RefreshCw className="size-3.5" />
                  </Button>
                  <Button size="icon" variant="ghost" className="size-7" aria-label="Delete" onClick={() => void action(style.id, "DELETE")}>
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
              {style.error && <p className="mt-1 text-xs text-red-700">{style.error}</p>}
              {style.summary && <p className="mt-1 text-xs text-muted-foreground">{style.summary}</p>}
              {style.videos.length > 0 && (
                <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                  {style.videos.map((video) => (
                    <a key={video.url} href={video.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-foreground">
                      <Youtube className="size-3" />{video.title.length > 48 ? `${video.title.slice(0, 46)}…` : video.title}
                    </a>
                  ))}
                </p>
              )}
              {style.guide && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-medium">Style guide</summary>
                  <p className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">{style.guide}</p>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
