"use client";

import { useRef, useState } from "react";
import { Loader2, Mic, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/studio/native-select";
import { readError, type StudioOptions, type StudioVoice } from "@/lib/studio";

const STATUS: Record<StudioVoice["status"], string> = {
  queued: "Waiting…",
  learning: "Learning your voice (the first time also downloads the voice model, ~130 MB)…",
  ready: "Ready",
  error: "Failed",
};

export function VoicesPanel({ voices, options, reload }: {
  voices: StudioVoice[]; options: StudioOptions | null; reload: () => Promise<void>;
}) {
  const [name, setName] = useState("My voice");
  const [base, setBase] = useState("sw-TZ-DaudiNeural");
  const [consent, setConsent] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setSaving(true);
    try {
      const form = new FormData();
      form.append("name", name);
      form.append("base_voice", base);
      form.append("consent", String(consent));
      form.append("sample", file);
      const response = await fetch("/api/studio/voices", { method: "POST", body: form });
      if (!response.ok) throw new Error(await readError(response, "Could not add the voice"));
      setFile(null);
      if (input.current) input.current.value = "";
      await reload();
      toast.success("Katakata is learning your voice. It will appear in the voice list when ready.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not add the voice");
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: string) {
    const response = await fetch(`/api/studio/voices/${id}`, { method: "DELETE" });
    if (!response.ok) toast.error(await readError(response, "Could not delete"));
    await reload();
  }

  return (
    <section className="rounded-2xl border bg-card p-4 sm:p-5" aria-labelledby="voices-heading">
      <h2 id="voices-heading" className="flex items-center gap-2 text-sm font-semibold"><Mic className="size-4" />My voice</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Record yourself talking naturally in Kiswahili for 1-3 minutes in a quiet room (no music), and upload it.
        Katakata keeps the timing of the base voice you pick and makes it sound like you. Runs on your PC, free.
      </p>
      <form onSubmit={add} className="mt-3 grid gap-2 sm:grid-cols-[160px_200px_1fr_auto] sm:items-center">
        <Input value={name} onChange={(event) => setName(event.target.value)} maxLength={60} aria-label="Voice name" />
        <NativeSelect value={base} onChange={setBase}>
          {(options?.voices || []).map((item) => <option key={item.id} value={item.id}>Based on {item.label}</option>)}
        </NativeSelect>
        <div className="flex min-w-0 items-center gap-2">
          <input
            ref={input} type="file" accept="audio/*,video/*" className="hidden"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
          <Button type="button" variant="outline" size="sm" onClick={() => input.current?.click()}>
            <Upload className="size-3.5" />{file ? "Change recording" : "Choose recording"}
          </Button>
          {file && <span className="truncate text-xs text-muted-foreground">{file.name}</span>}
        </div>
        <Button type="submit" disabled={saving || !file || !consent || name.trim().length < 2}>
          {saving ? <Loader2 className="size-4 animate-spin" /> : <Mic className="size-4" />}Clone
        </Button>
      </form>
      <label className="mt-2 flex items-start gap-2 text-xs text-muted-foreground">
        <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} className="mt-0.5 size-3.5" />
        This is my own voice, or I have the speaker&apos;s permission to clone it.
      </label>
      <p className="mt-1 text-[11px] text-muted-foreground">Tip: pick the base voice closest to yours (Daudi or Rafiki for a male voice, Rehema or Zuri for a female voice).</p>
      {voices.length > 0 && (
        <ul className="mt-4 space-y-2">
          {voices.map((voice) => (
            <li key={voice.id} className="flex flex-wrap items-center gap-2 rounded-xl border bg-background p-3 text-sm">
              <span className="font-medium">{voice.name}</span>
              <span className={voice.status === "error" ? "text-xs text-red-700" : "text-xs text-muted-foreground"}>
                {voice.status !== "ready" && voice.status !== "error" && <Loader2 className="mr-1 inline size-3 animate-spin" />}
                {voice.status === "error" ? voice.error || "Failed" : STATUS[voice.status]}
                {voice.status === "ready" && voice.speech_seconds ? ` · learned from ${Math.round(voice.speech_seconds)}s of speech` : ""}
              </span>
              <Button size="icon" variant="ghost" className="ml-auto size-7" aria-label="Delete voice" onClick={() => void remove(voice.id)}>
                <Trash2 className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
