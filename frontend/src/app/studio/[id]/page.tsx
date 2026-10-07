"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft, Check, CheckCircle2, Clapperboard, Copy, Download, ExternalLink, Film, Loader2,
  RefreshCw, Trash2, Upload, Wand2,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { NativeSelect } from "@/components/studio/native-select";
import { cn } from "@/lib/utils";
import {
  BUSY_STATUSES, STAGE_LABELS, STATUS_LABELS, formatDuration, readError,
  type Production, type StudioOptions, type StudioScene,
} from "@/lib/studio";

const FLOW_URL = "https://labs.google/fx/tools/flow";

export default function ProductionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [production, setProduction] = useState<Production | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [options, setOptions] = useState<StudioOptions | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const load = useCallback(async () => {
    const response = await fetch(`/api/studio/${id}`, { cache: "no-store" });
    if (response.status === 404) {
      setNotFound(true);
      return;
    }
    if (response.ok) setProduction(await response.json());
  }, [id]);

  useEffect(() => {
    void load();
    fetch("/api/studio/options", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then(setOptions)
      .catch(() => undefined);
  }, [load]);

  const busy = production ? BUSY_STATUSES.includes(production.status) && !production.stuck : false;
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [busy, load]);

  const act = useCallback(async (name: string, path: string, init: RequestInit = { method: "POST" }) => {
    setBusyAction(name);
    try {
      const response = await fetch(`/api/studio/${id}${path}`, init);
      if (!response.ok) throw new Error(await readError(response, "That did not work"));
      await load();
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "That did not work");
      return false;
    } finally {
      setBusyAction(null);
    }
  }, [id, load]);

  if (notFound) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16 text-center">
        <p className="text-sm text-muted-foreground">This Studio video does not exist or was deleted.</p>
        <Button asChild variant="outline" className="mt-4"><Link href="/studio">Back to Studio</Link></Button>
      </div>
    );
  }
  if (!production) {
    return (
      <div className="mx-auto max-w-5xl space-y-3 px-4 py-8">
        <Skeleton className="h-8 w-64" /><Skeleton className="h-40 w-full" /><Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const { proposal, prompts, brief } = production;
  const approved = Boolean(production.approved_at && prompts);

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 pb-24 sm:px-6">
      <Link href="/studio" className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" />Studio
      </Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-bold tracking-tight">{proposal?.title || "New Studio video"}</h1>
          {proposal?.title_english && <p className="text-sm text-muted-foreground">{proposal.title_english}</p>}
          <p className="mt-1 text-xs text-muted-foreground">
            {brief.aspect_ratio} · {formatDuration(brief.duration_seconds)} · Cinematic Story
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={cn(
            "rounded-full px-2.5 py-1 text-xs font-medium",
            production.status === "done" ? "bg-emerald-100 text-emerald-800"
              : production.status === "error" ? "bg-red-100 text-red-700"
                : busy ? "bg-amber-100 text-amber-800" : "bg-secondary text-secondary-foreground",
          )}>{STATUS_LABELS[production.status]}</span>
          <Button
            variant="ghost" size="icon" aria-label="Delete this video" disabled={busy || busyAction !== null}
            onClick={async () => {
              if (!window.confirm("Delete this Studio video, its clips and the final video?")) return;
              if (await act("delete", "", { method: "DELETE" })) router.push("/studio");
            }}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      </div>

      <Steps production={production} />

      {busy && (
        <div className="mb-6 flex items-center gap-3 rounded-xl border bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <Loader2 className="size-4 animate-spin" />
          {production.progress_message || "Working…"}
          <span className="ml-auto text-xs text-amber-800/80">You can leave this page; it keeps going.</span>
        </div>
      )}

      {(production.status === "error" || production.stuck) && (
        <Alert className="mb-6 border-red-200 bg-red-50">
          <AlertDescription className="flex flex-wrap items-center gap-3 text-sm text-red-700">
            <span className="min-w-0 flex-1 break-words">
              {production.stuck ? "This step stopped responding (Katakata may have restarted)." : production.error || "Something went wrong."}
            </span>
            <Button size="sm" variant="outline" disabled={busyAction !== null} onClick={() => void act("retry", "/retry")}>
              <RefreshCw className="size-3.5" />Try again
            </Button>
          </AlertDescription>
        </Alert>
      )}

      <IdeaCard production={production} />

      {production.research && production.research.sources.length > 0 && <ResearchCard production={production} />}

      {proposal && (
        <ProposalSection
          production={production}
          disabled={busy || busyAction !== null}
          approved={approved}
          act={act}
          busyAction={busyAction}
        />
      )}

      {proposal && prompts && (
        <ShootingSection production={production} disabled={busy} reload={load} />
      )}

      {proposal && prompts && (
        <RenderSection production={production} options={options} disabled={busy || busyAction !== null} act={act} busyAction={busyAction} />
      )}

      {production.final && <FinalSection production={production} />}
    </div>
  );
}

function Steps({ production }: { production: Production }) {
  const clipsDone = Object.values(production.clips || {}).filter(Boolean).length;
  const total = production.proposal?.scenes.length || 0;
  const steps = [
    { label: "Script", done: Boolean(production.proposal) },
    { label: "Approve", done: Boolean(production.approved_at && production.prompts) },
    { label: total ? `Clips ${clipsDone}/${total}` : "Clips", done: total > 0 && clipsDone === total },
    { label: "Video", done: Boolean(production.final) },
  ];
  return (
    <ol className="mb-6 grid grid-cols-4 gap-2 text-xs">
      {steps.map((step, index) => (
        <li key={step.label} className={cn("flex items-center gap-1.5 rounded-lg border px-2 py-2", step.done ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "bg-card text-muted-foreground")}>
          {step.done ? <CheckCircle2 className="size-3.5 shrink-0" /> : <span className="grid size-4 shrink-0 place-items-center rounded-full border text-[10px]">{index + 1}</span>}
          <span className="truncate">{step.label}</span>
        </li>
      ))}
    </ol>
  );
}

function IdeaCard({ production }: { production: Production }) {
  return (
    <section className="mb-6 rounded-xl border bg-card p-4">
      <h2 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Your idea</h2>
      <p className="whitespace-pre-wrap text-sm">{production.brief.idea}</p>
    </section>
  );
}

function ResearchCard({ production }: { production: Production }) {
  const sources = production.research?.sources || [];
  return (
    <details className="mb-6 rounded-xl border bg-card p-4">
      <summary className="cursor-pointer text-sm font-medium">Facts looked up ({sources.length} sources)</summary>
      <ol className="mt-3 space-y-3">
        {sources.map((source, index) => (
          <li key={source.url} className="text-xs">
            <a href={source.url} target="_blank" rel="noopener noreferrer" className="font-medium text-foreground underline-offset-2 hover:underline">
              [{index + 1}] {source.title}
            </a>
            <p className="mt-0.5 line-clamp-3 text-muted-foreground">{source.extract}</p>
          </li>
        ))}
      </ol>
    </details>
  );
}

function ProposalSection({ production, disabled, approved, act, busyAction }: {
  production: Production;
  disabled: boolean;
  approved: boolean;
  act: (name: string, path: string, init?: RequestInit) => Promise<boolean>;
  busyAction: string | null;
}) {
  const proposal = production.proposal!;
  const [feedback, setFeedback] = useState("");

  return (
    <section className="mb-8">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <h2 className="text-lg font-semibold">Script and scenes</h2>
        <span className="text-xs text-muted-foreground">Version {production.revision}</span>
      </div>
      <div className="mb-4 grid gap-3 rounded-xl border bg-card p-4 text-sm sm:grid-cols-2">
        <Info label="Core message" value={proposal.core_message} />
        <Info label="On-screen headline" value={proposal.hook_title} />
        <Info label="Tone" value={proposal.tone} />
        <Info label="Music mood" value={proposal.music_mood} />
      </div>

      <div className="space-y-3">
        {proposal.scenes.map((scene) => (
          <SceneCard key={`${production.revision}-${scene.number}`} productionId={production.id} scene={scene} disabled={disabled} />
        ))}
      </div>

      {proposal.fact_check_notes.length > 0 && (
        <details className="mt-4 rounded-xl border bg-card p-4">
          <summary className="cursor-pointer text-sm font-medium">Fact check ({proposal.fact_check_notes.length} claims)</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
            {proposal.fact_check_notes.map((note) => <li key={note}>{note}</li>)}
          </ul>
        </details>
      )}

      <div className="mt-4 rounded-xl border bg-card p-4">
        <label htmlFor="feedback" className="mb-1.5 block text-sm font-medium">Want changes? Tell the director</label>
        <Textarea
          id="feedback" rows={2} value={feedback} onChange={(event) => setFeedback(event.target.value)}
          placeholder="e.g. Make the hook more shocking, use a Kariakoo market scene, end with a question about family"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            variant="outline" disabled={disabled || feedback.trim().length < 3}
            onClick={async () => { if (await act("revise", "/revise", jsonInit({ feedback }))) setFeedback(""); }}
          >
            {busyAction === "revise" ? <Loader2 className="size-4 animate-spin" /> : <Wand2 className="size-4" />}
            Rewrite with my notes
          </Button>
          <Button disabled={disabled} onClick={() => void act("approve", "/approve")}>
            {busyAction === "approve" ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
            {approved ? "Approve again and rewrite prompts" : "Approve script"}
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Editing a line of narration below is saved straight away and does not need a new approval.
        </p>
      </div>
    </section>
  );
}

function SceneCard({ productionId, scene, disabled }: { productionId: string; scene: StudioScene; disabled: boolean }) {
  const [narration, setNarration] = useState(scene.narration);
  const [overlay, setOverlay] = useState(scene.overlay_text);
  const [saving, setSaving] = useState(false);
  const words = narration.trim().split(/\s+/).filter(Boolean).length;

  async function save(changes: Record<string, string>) {
    setSaving(true);
    try {
      const response = await fetch(`/api/studio/${productionId}/scenes/${scene.number}`, jsonInit(changes, "PATCH"));
      if (!response.ok) throw new Error(await readError(response, "Could not save"));
      toast.success(`Scene ${scene.number} saved`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <article className="rounded-xl border bg-card p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="grid size-6 place-items-center rounded-full bg-foreground text-xs font-bold text-background">{scene.number}</span>
        <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-medium">{STAGE_LABELS[scene.stage] || scene.stage}</span>
        {saving && <Loader2 className="ml-auto size-3.5 animate-spin text-muted-foreground" />}
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <label className="mb-1 flex items-baseline justify-between text-xs font-medium text-muted-foreground">
            Kiswahili narration
            <span className={cn(words > 26 && "text-amber-700")}>{words} words</span>
          </label>
          <Textarea
            value={narration} rows={3} disabled={disabled}
            onChange={(event) => setNarration(event.target.value)}
            onBlur={() => { if (narration.trim() && narration !== scene.narration) void save({ narration }); }}
            className="text-sm"
          />
          <p className="mt-1.5 text-xs italic text-muted-foreground">{scene.narration_english}</p>
          <label className="mb-1 mt-3 block text-xs font-medium text-muted-foreground">On-screen phrase (optional)</label>
          <Input
            value={overlay} maxLength={60} disabled={disabled}
            onChange={(event) => setOverlay(event.target.value)}
            onBlur={() => { if (overlay !== scene.overlay_text) void save({ overlay_text: overlay }); }}
            className="h-8 text-sm"
          />
        </div>
        <div className="space-y-2 text-xs">
          <p><span className="font-medium">Setting:</span> <span className="text-muted-foreground">{scene.setting}</span></p>
          <ul className="space-y-1">
            {scene.beats.map((beat) => (
              <li key={beat.window}><span className="font-mono text-[11px] text-muted-foreground">[{beat.window}]</span> {beat.action}</li>
            ))}
          </ul>
          <p><span className="font-medium">Camera:</span> <span className="text-muted-foreground">{scene.camera}</span></p>
          <p><span className="font-medium">Sound:</span> <span className="text-muted-foreground">{scene.sound_effects}</span></p>
        </div>
      </div>
    </article>
  );
}

function ShootingSection({ production, disabled, reload }: { production: Production; disabled: boolean; reload: () => Promise<void> }) {
  const prompts = production.prompts!;
  const scenes = production.proposal!.scenes;
  const done = scenes.filter((scene) => production.clips[String(scene.number)]).length;
  return (
    <section className="mb-8">
      <h2 className="mb-1 text-lg font-semibold">Make the clips ({done}/{scenes.length})</h2>
      <ol className="mb-4 list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
        <li>Copy a scene&apos;s prompt and paste it into <a href={FLOW_URL} target="_blank" rel="noopener noreferrer" className="font-medium text-foreground underline underline-offset-2">Google Flow</a> (Gemini Omni Flash, {production.brief.aspect_ratio}).</li>
        <li>Download the clip you like and upload it to that scene here.</li>
        <li>When every scene has a clip, render. Katakata adds the Kiswahili voice, captions and music.</li>
      </ol>
      <details className="mb-4 rounded-xl border bg-card p-4 text-xs">
        <summary className="cursor-pointer text-sm font-medium">Continuity notes</summary>
        <p className="mt-2 whitespace-pre-wrap text-muted-foreground">{prompts.continuity}</p>
      </details>
      <div className="space-y-3">
        {prompts.prompts.map((item) => (
          <ShotCard
            key={item.number}
            productionId={production.id}
            number={item.number}
            prompt={item.prompt}
            hasClip={Boolean(production.clips[String(item.number)])}
            disabled={disabled}
            reload={reload}
          />
        ))}
      </div>
    </section>
  );
}

function ShotCard({ productionId, number, prompt, hasClip, disabled, reload }: {
  productionId: string; number: number; prompt: string; hasClip: boolean; disabled: boolean; reload: () => Promise<void>;
}) {
  const [uploading, setUploading] = useState(false);
  const [version, setVersion] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  async function upload(file: File) {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("clip", file);
      const response = await fetch(`/api/studio/${productionId}/scenes/${number}/clip`, { method: "POST", body: form });
      if (!response.ok) throw new Error(await readError(response, "Upload failed"));
      setVersion((value) => value + 1);
      await reload();
      toast.success(`Scene ${number} clip uploaded`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <article className="grid gap-3 rounded-xl border bg-card p-4 md:grid-cols-[1fr_200px]">
      <div className="min-w-0">
        <div className="mb-2 flex items-center gap-2">
          <span className="grid size-6 place-items-center rounded-full bg-foreground text-xs font-bold text-background">{number}</span>
          <span className="text-sm font-medium">Scene {number} prompt</span>
          <Button
            size="sm" variant="outline" className="ml-auto h-7"
            onClick={async () => {
              await navigator.clipboard.writeText(prompt);
              toast.success(`Scene ${number} prompt copied`);
            }}
          >
            <Copy className="size-3.5" />Copy
          </Button>
        </div>
        <p className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">{prompt}</p>
      </div>
      <div className="flex flex-col gap-2">
        {hasClip ? (
          <video
            key={version}
            src={`/api/studio/${productionId}/files/scene-${number}?v=${version}`}
            className="aspect-video w-full rounded-lg bg-black object-contain"
            controls muted playsInline preload="metadata"
          />
        ) : (
          <div className="grid aspect-video w-full place-items-center rounded-lg border border-dashed text-xs text-muted-foreground">
            <span className="flex items-center gap-1"><Film className="size-4" />No clip yet</span>
          </div>
        )}
        <input
          ref={input} type="file" accept="video/*" className="hidden"
          onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); }}
        />
        <Button size="sm" variant={hasClip ? "outline" : "default"} disabled={disabled || uploading} onClick={() => input.current?.click()}>
          {uploading ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}
          {hasClip ? "Replace clip" : "Upload clip"}
        </Button>
        <a href={FLOW_URL} target="_blank" rel="noopener noreferrer" className="inline-flex items-center justify-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          Open Google Flow <ExternalLink className="size-3" />
        </a>
      </div>
    </article>
  );
}

function RenderSection({ production, options, disabled, act, busyAction }: {
  production: Production;
  options: StudioOptions | null;
  disabled: boolean;
  act: (name: string, path: string, init?: RequestInit) => Promise<boolean>;
  busyAction: string | null;
}) {
  const { brief } = production;
  const [voice, setVoice] = useState(brief.voice);
  const [speed, setSpeed] = useState(brief.voice_speed || 0);
  const [captions, setCaptions] = useState(brief.captions);
  const [template, setTemplate] = useState(brief.caption_template || "default");
  const [hookTitle, setHookTitle] = useState(brief.hook_title);
  const [overlays, setOverlays] = useState(brief.overlays);
  const [music, setMusic] = useState(brief.music || "");
  const [musicVolume, setMusicVolume] = useState(brief.music_volume ?? 0.12);
  const scenes = production.proposal!.scenes;
  const ready = scenes.every((scene) => production.clips[String(scene.number)]);

  return (
    <section className="mb-8 rounded-xl border bg-card p-4">
      <h2 className="mb-3 text-lg font-semibold">Voice, captions and music</h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Setting label="Voice">
          <NativeSelect value={voice} onChange={setVoice}>
            {(options?.voices || [{ id: brief.voice, label: brief.voice }]).map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </NativeSelect>
        </Setting>
        <Setting label={`Speaking pace ${speed > 0 ? "+" : ""}${speed}%`}>
          <input type="range" min={-20} max={20} step={2} value={speed} onChange={(event) => setSpeed(Number(event.target.value))} className="w-full" />
        </Setting>
        <Setting label="Caption style">
          <NativeSelect value={template} onChange={setTemplate}>
            {(options?.caption_templates || ["default"]).map((name) => <option key={name} value={name}>{name.replace(/_/g, " ")}</option>)}
          </NativeSelect>
        </Setting>
        <Setting label="Music">
          <NativeSelect value={music} onChange={setMusic}>
            <option value="">No music</option>
            {options?.music.length ? <option value="random">Random track</option> : null}
            {(options?.music || []).map((track) => <option key={track} value={track}>{track}</option>)}
          </NativeSelect>
          {!options?.music.length && <p className="mt-1 text-[11px] text-muted-foreground">Add music files to backend/music to use them.</p>}
        </Setting>
      </div>
      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm">
        <Toggle checked={captions} onChange={setCaptions} label="Word-by-word captions" />
        <Toggle checked={hookTitle} onChange={setHookTitle} label="Headline at the start" />
        <Toggle checked={overlays} onChange={setOverlays} label="On-screen phrases" />
        {music && (
          <label className="flex items-center gap-2 text-sm">
            Music level
            <input type="range" min={0.04} max={0.3} step={0.02} value={musicVolume} onChange={(event) => setMusicVolume(Number(event.target.value))} />
          </label>
        )}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button
          disabled={disabled || !ready}
          onClick={() => void act("render", "/render", jsonInit({
            voice, voice_speed: speed, captions, caption_template: template, hook_title: hookTitle,
            overlays, music, music_volume: musicVolume,
          }))}
        >
          {busyAction === "render" ? <Loader2 className="size-4 animate-spin" /> : <Clapperboard className="size-4" />}
          {production.final ? "Render again" : "Render the video"}
        </Button>
        {!ready && <span className="text-xs text-muted-foreground">Upload a clip for every scene first.</span>}
      </div>
    </section>
  );
}

function FinalSection({ production }: { production: Production }) {
  const final = production.final!;
  const proposal = production.proposal;
  const postText = proposal ? `${proposal.post_caption}\n\n${proposal.hashtags.join(" ")}` : "";
  const src = `/api/studio/${production.id}/files/final?v=${encodeURIComponent(final.rendered_at)}`;
  return (
    <section className="mb-8 rounded-xl border bg-card p-4">
      <h2 className="mb-3 text-lg font-semibold">Your video</h2>
      <div className="grid gap-4 md:grid-cols-[minmax(0,320px)_1fr]">
        <video
          src={src} controls playsInline
          className={cn("w-full rounded-lg bg-black", production.brief.aspect_ratio === "9:16" ? "aspect-[9/16]" : "aspect-video")}
        />
        <div className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            {formatDuration(Math.round(final.duration))} · saved to your clips folder as <span className="font-mono text-xs">studio/{final.filename}</span>
          </p>
          <Button asChild variant="outline"><a href={src} download={final.filename}><Download className="size-4" />Download</a></Button>
          {proposal && (
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">Post caption and hashtags</span>
                <Button size="sm" variant="ghost" className="h-7" onClick={async () => { await navigator.clipboard.writeText(postText); toast.success("Copied"); }}>
                  <Copy className="size-3.5" />Copy
                </Button>
              </div>
              <p className="whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-sm">{postText}</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p>{value}</p>
    </div>
  );
}

function Setting({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="mb-1.5 block text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return (
    <label className="flex items-center gap-2">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="size-4" />
      {label}
    </label>
  );
}

function jsonInit(body: unknown, method = "POST"): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}
