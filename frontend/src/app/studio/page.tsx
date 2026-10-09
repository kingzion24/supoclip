"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Clapperboard, Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import { NativeSelect } from "@/components/studio/native-select";
import { StylesPanel } from "@/components/studio/styles-panel";
import { VoicesPanel } from "@/components/studio/voices-panel";
import {
  BUSY_STATUSES, STATUS_LABELS, formatDuration, readError,
  type Genre, type ProductionSummary, type StudioOptions, type StudioStyle, type StudioVoice,
} from "@/lib/studio";

const IDEAS = [
  "Kwa nini vijana wengi Dar es Salaam wanashindwa kuweka akiba, na tabia tatu ndogo za kuanza leo",
  "Historia ya Vita vya Maji Maji kwa dakika moja: kwa nini ilianza na tunajifunza nini",
  "Saikolojia ya kuahirisha mambo: ubongo wako unakudanganya vipi",
  "Jinsi M-Pesa ilivyobadilisha maisha Afrika Mashariki",
];
const DOCUMENTARY_IDEAS = [
  "Documentary: kuanguka kwa Dola ya Kilwa, mji tajiri wa biashara wa pwani ya Afrika Mashariki",
  "Documentary: hadithi ya Muungano wa Tanganyika na Zanzibar mwaka 1964",
  "Documentary: jinsi Bitcoin ilivyozaliwa na kwa nini benki kuu zinaiogopa",
];

export default function StudioPage() {
  const router = useRouter();
  const [options, setOptions] = useState<StudioOptions | null>(null);
  const [productions, setProductions] = useState<ProductionSummary[] | null>(null);
  const [idea, setIdea] = useState("");
  const [aspect, setAspect] = useState<"9:16" | "16:9">("9:16");
  const [duration, setDuration] = useState(60);
  const [voice, setVoice] = useState("sw-TZ-DaudiNeural");
  const [research, setResearch] = useState(true);
  const [genre, setGenre] = useState<Genre>("explainer");
  const [styleId, setStyleId] = useState("");
  const [styles, setStyles] = useState<StudioStyle[]>([]);
  const [voices, setVoices] = useState<StudioVoice[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAssets = useCallback(async () => {
    const [styleResponse, voiceResponse] = await Promise.all([
      fetch("/api/studio/styles", { cache: "no-store" }).catch(() => null),
      fetch("/api/studio/voices", { cache: "no-store" }).catch(() => null),
    ]);
    if (styleResponse?.ok) setStyles((await styleResponse.json()).styles || []);
    if (voiceResponse?.ok) setVoices((await voiceResponse.json()).voices || []);
  }, []);

  const assetsBusy = styles.some((item) => !["ready", "error"].includes(item.status))
    || voices.some((item) => !["ready", "error"].includes(item.status));
  useEffect(() => {
    void loadAssets();
  }, [loadAssets]);
  useEffect(() => {
    if (!assetsBusy) return;
    const timer = window.setInterval(() => void loadAssets(), 4000);
    return () => window.clearInterval(timer);
  }, [assetsBusy, loadAssets]);

  const durationChoices = options?.genres?.[genre] || (genre === "documentary" ? [120, 180, 300, 420, 600] : [30, 60, 90, 120, 180]);
  function chooseGenre(value: Genre) {
    setGenre(value);
    setDuration(value === "documentary" ? 300 : 60);
    if (value === "documentary") setAspect("16:9");
  }
  const readyVoices = voices.filter((item) => item.status === "ready");
  const readyStyles = styles.filter((item) => item.status === "ready");

  useEffect(() => {
    fetch("/api/studio/options", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data: StudioOptions | null) => {
        if (data) {
          setOptions(data);
          setVoice(data.default_voice);
        }
      })
      .catch(() => undefined);
    fetch("/api/studio", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : { productions: [] }))
      .then((data) => setProductions(data.productions || []))
      .catch(() => setProductions([]));
  }, []);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const response = await fetch("/api/studio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idea, genre, aspect_ratio: aspect, duration_seconds: duration, voice, research, style_id: styleId || null,
        }),
      });
      if (!response.ok) throw new Error(await readError(response, "Could not start the video"));
      const production = await response.json();
      router.push(`/studio/${production.id}`);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "Could not start the video");
      setCreating(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6">
      <div className="mb-6">
        <h1 className="flex items-center gap-2 font-display text-2xl font-bold tracking-tight">
          <Clapperboard className="size-6" />Studio
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Describe a video. Katakata researches it, writes a Kiswahili script and scene-by-scene stickman plan,
          then voices and assembles the final video from the clips you generate.
        </p>
      </div>

      <form onSubmit={create} className="space-y-4 rounded-2xl border bg-card p-4 sm:p-5">
        <div>
          <label htmlFor="idea" className="mb-1.5 block text-sm font-medium">What should the video be about?</label>
          <Textarea
            id="idea"
            value={idea}
            onChange={(event) => setIdea(event.target.value)}
            placeholder="Andika kwa Kiswahili au Kiingereza: mada, ujumbe, hadhira, na mambo muhimu ya kutaja…"
            rows={4}
            maxLength={4000}
          />
          <div className="mt-2 flex flex-wrap gap-1.5">
            {(genre === "documentary" ? DOCUMENTARY_IDEAS : IDEAS).map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => setIdea(example)}
                className="rounded-full border bg-background px-2.5 py-1 text-left text-xs text-muted-foreground hover:border-foreground/40 hover:text-foreground"
              >
                {example.length > 60 ? `${example.slice(0, 58)}…` : example}
              </button>
            ))}
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Genre">
            <div className="inline-flex rounded-lg border bg-background p-0.5">
              {([["explainer", "Explainer short"], ["documentary", "Documentary"]] as const).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={genre === value}
                  onClick={() => chooseGenre(value)}
                  className={cn("h-8 rounded-md px-3 text-xs font-medium", genre === value ? "bg-foreground text-background" : "text-muted-foreground")}
                >
                  {label}
                </button>
              ))}
            </div>
          </Field>
          <Field label="Inspiration">
            <NativeSelect value={styleId} onChange={setStyleId}>
              <option value="">None</option>
              {readyStyles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </NativeSelect>
          </Field>
          <Field label="Format">
            <div className="inline-flex rounded-lg border bg-background p-0.5">
              {(["9:16", "16:9"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={aspect === value}
                  onClick={() => setAspect(value)}
                  className={cn("h-8 rounded-md px-3 text-xs font-medium", aspect === value ? "bg-foreground text-background" : "text-muted-foreground")}
                >
                  {value === "9:16" ? "9:16 Shorts" : "16:9 YouTube"}
                </button>
              ))}
            </div>
          </Field>
          <Field label="Length">
            <NativeSelect value={String(duration)} onChange={(value) => setDuration(Number(value))}>
              {durationChoices.map((value) => (
                <option key={value} value={value}>{formatDuration(value)} · {value / 10} scenes</option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Voice">
            <NativeSelect value={voice} onChange={setVoice}>
              {readyVoices.map((item) => <option key={item.id} value={`custom:${item.id}`}>{item.name} (my voice)</option>)}
              {(options?.voices || [{ id: "sw-TZ-DaudiNeural", label: "Daudi (Tanzania, male)" }]).map((item) => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Facts">
            <label className="flex h-9 items-center gap-2 text-sm">
              <input type="checkbox" checked={research} onChange={(event) => setResearch(event.target.checked)} className="size-4" />
              Look up facts first
            </label>
          </Field>
        </div>

        <p className="text-xs text-muted-foreground">
          Style: <span className="font-medium text-foreground">Cinematic Story</span>, a red-beanie stick figure in full-color scenes.
          {genre === "documentary"
            ? " Documentaries are written in chapters with a cold open, rising tension and a turning point."
            : " Explainers follow a 5-stage hook-to-question arc."}
          {" "}You review and edit the script before anything is generated.
        </p>

        {error && (
          <Alert className="border-red-200 bg-red-50">
            <AlertDescription className="text-sm text-red-700">{error}</AlertDescription>
          </Alert>
        )}

        <Button type="submit" disabled={creating || idea.trim().length < 10}>
          {creating ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
          Write the script
        </Button>
      </form>

      <div className="mt-6 grid gap-4">
        <StylesPanel styles={styles} reload={loadAssets} />
        <VoicesPanel voices={voices} options={options} reload={loadAssets} />
      </div>

      <section className="mt-10" aria-labelledby="productions-heading">
        <h2 id="productions-heading" className="mb-3 text-sm font-medium text-muted-foreground">Your Studio videos</h2>
        {productions === null ? (
          <div className="space-y-2">{[0, 1, 2].map((index) => <Skeleton key={index} className="h-14 w-full rounded-xl" />)}</div>
        ) : productions.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing yet. Describe your first video above.</p>
        ) : (
          <ul className="divide-y rounded-xl border bg-card">
            {productions.map((item) => (
              <li key={item.id}>
                <Link href={`/studio/${item.id}`} className="flex items-center gap-3 px-4 py-3 hover:bg-accent/50">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{item.title}</p>
                    <p className="text-xs text-muted-foreground">
                      {item.aspect_ratio} · {formatDuration(item.duration_seconds)} · {new Date(item.created_at).toLocaleDateString()}
                    </p>
                  </div>
                  <span className={cn(
                    "shrink-0 rounded-full px-2 py-0.5 text-xs font-medium",
                    item.status === "done" ? "bg-emerald-100 text-emerald-800"
                      : item.status === "error" ? "bg-red-100 text-red-700"
                        : BUSY_STATUSES.includes(item.status) ? "bg-amber-100 text-amber-800" : "bg-secondary text-secondary-foreground",
                  )}>
                    {STATUS_LABELS[item.status] || item.status}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="mb-1.5 block text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

