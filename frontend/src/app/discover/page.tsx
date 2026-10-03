"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Compass, Flame, Loader2, Scissors, Search, Youtube } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { cn } from "@/lib/utils";

interface TrendingSearch {
  title: string;
  traffic: string;
  news: string[];
}

interface PopularVideo {
  id: string;
  title: string;
  channel: string;
  views: number;
  url: string;
}

interface Trends {
  region: string;
  region_name: string;
  searches: TrendingSearch[];
  videos: PopularVideo[];
}

interface SearchResult {
  id: string;
  title: string;
  channel: string;
  duration: number | null;
  views: number | null;
  thumbnail: string;
  url: string;
}

type Kind = "videos" | "podcasts";

function formatDuration(seconds: number | null) {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function formatViews(views: number | null) {
  if (!views) return "";
  return `${new Intl.NumberFormat("en", { notation: "compact" }).format(views)} views`;
}

function clipHref(url: string) {
  return `/?url=${encodeURIComponent(url)}`;
}

export default function DiscoverPage() {
  const [trends, setTrends] = useState<Trends | null>(null);
  const [trendsLoading, setTrendsLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<Kind>("videos");
  const [longOnly, setLongOnly] = useState(true);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/discover/trending", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data: Trends | null) => setTrends(data))
      .catch(() => setTrends(null))
      .finally(() => setTrendsLoading(false));
  }, []);

  const runSearch = useCallback(async (term: string, searchKind: Kind = kind) => {
    const cleaned = term.trim();
    if (cleaned.length < 2) return;
    setQuery(cleaned);
    setSearching(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        q: cleaned,
        kind: searchKind,
        limit: "12",
        min_minutes: longOnly ? "8" : "0",
      });
      const response = await fetch(`/api/discover/search?${params}`, { cache: "no-store" });
      if (!response.ok) throw new Error(formatSupportMessage(await parseApiError(response, "Search failed")));
      const data = await response.json();
      setResults(data.results || []);
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : "Search failed");
      setResults(null);
    } finally {
      setSearching(false);
    }
  }, [kind, longOnly]);

  const hasTrends = Boolean(trends && (trends.searches.length || trends.videos.length));

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
      <div className="mb-6">
        <h1 className="flex items-center gap-2 font-display text-2xl font-bold tracking-tight">
          <Compass className="size-6" />Discover
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Find videos and podcasts worth clipping, and see what people are searching for right now.
        </p>
      </div>

      <form
        onSubmit={(event) => { event.preventDefault(); void runSearch(query); }}
        className="flex flex-col gap-3 rounded-2xl border bg-card p-3 sm:flex-row sm:items-center"
      >
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search YouTube, e.g. biashara Tanzania, Bongo Flava interview"
            aria-label="Search YouTube"
            className="h-10 pl-9"
          />
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-lg border bg-background p-0.5" role="tablist" aria-label="What to search">
            {([["videos", "Videos"], ["podcasts", "Podcasts"]] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={kind === value}
                onClick={() => { setKind(value); if (query.trim().length >= 2) void runSearch(query, value); }}
                className={cn("h-8 rounded-md px-3 text-xs font-medium transition-colors", kind === value ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground")}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input type="checkbox" checked={longOnly} onChange={(event) => setLongOnly(event.target.checked)} className="size-3.5" />
            8+ min only
          </label>
          <Button type="submit" disabled={searching || query.trim().length < 2} className="h-10">
            {searching ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
            Search
          </Button>
        </div>
      </form>

      {error && (
        <Alert className="mt-4 border-red-200 bg-red-50">
          <AlertDescription className="text-sm text-red-700">{error}</AlertDescription>
        </Alert>
      )}

      {(searching || results) && (
        <section className="mt-8" aria-labelledby="results-heading">
          <h2 id="results-heading" className="mb-3 text-sm font-medium text-muted-foreground">
            {searching ? "Searching…" : `${results?.length ?? 0} results for “${query}”`}
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {searching
              ? Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="aspect-video w-full rounded-xl" />)
              : results?.map((video) => (
                <article key={video.id} className="flex flex-col overflow-hidden rounded-xl border bg-card">
                  <a href={video.url} target="_blank" rel="noopener noreferrer" className="relative block aspect-video bg-muted">
                    {/* eslint-disable-next-line @next/next/no-img-element -- YouTube thumbnails, not optimised assets */}
                    <img src={video.thumbnail} alt="" loading="lazy" className="size-full object-cover" />
                    {video.duration ? (
                      <span className="absolute bottom-2 right-2 rounded bg-black/75 px-1.5 py-0.5 text-[11px] font-medium text-white">
                        {formatDuration(video.duration)}
                      </span>
                    ) : null}
                  </a>
                  <div className="flex flex-1 flex-col gap-2 p-3">
                    <h3 className="line-clamp-2 text-sm font-medium leading-snug">{video.title}</h3>
                    <p className="text-xs text-muted-foreground">
                      {[video.channel, formatViews(video.views)].filter(Boolean).join(" · ")}
                    </p>
                    <Button asChild size="sm" className="mt-auto w-full">
                      <Link href={clipHref(video.url)}><Scissors className="size-3.5" />Clip this</Link>
                    </Button>
                  </div>
                </article>
              ))}
          </div>
          {!searching && results?.length === 0 && (
            <p className="text-sm text-muted-foreground">No clippable videos found. Try other words or turn off “8+ min only”.</p>
          )}
        </section>
      )}

      <section className="mt-10" aria-labelledby="trending-heading">
        <h2 id="trending-heading" className="mb-1 flex items-center gap-2 text-lg font-semibold">
          <Flame className="size-5 text-orange-500" />
          Trending{trends?.region_name ? ` in ${trends.region_name}` : ""}
        </h2>
        <p className="mb-4 text-xs text-muted-foreground">
          Katakata also uses these trends when it picks clips and writes hashtags. Click a topic to search for videos about it.
        </p>
        {trendsLoading ? (
          <div className="flex flex-wrap gap-2">
            {Array.from({ length: 8 }, (_, index) => <Skeleton key={index} className="h-8 w-28 rounded-full" />)}
          </div>
        ) : !hasTrends ? (
          <p className="text-sm text-muted-foreground">
            Trends are unavailable right now (offline, or TRENDS_REGION is empty in .env).
          </p>
        ) : (
          <div className="space-y-6">
            {trends!.searches.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {trends!.searches.map((search) => (
                  <button
                    key={search.title}
                    type="button"
                    onClick={() => void runSearch(search.title)}
                    title={search.news[0] || undefined}
                    className="rounded-full border bg-background px-3 py-1.5 text-sm transition-colors hover:border-foreground/40 hover:bg-accent"
                  >
                    {search.title}
                    {search.traffic && <span className="ml-1.5 text-xs text-muted-foreground">{search.traffic}</span>}
                  </button>
                ))}
              </div>
            )}
            {trends!.videos.length > 0 && (
              <div>
                <h3 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
                  <Youtube className="size-4" />Popular on YouTube
                </h3>
                <ul className="divide-y rounded-xl border bg-card">
                  {trends!.videos.map((video) => (
                    <li key={video.id} className="flex items-center gap-3 px-3 py-2">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm">{video.title}</p>
                        <p className="text-xs text-muted-foreground">
                          {[video.channel, formatViews(video.views)].filter(Boolean).join(" · ")}
                        </p>
                      </div>
                      <Button asChild size="sm" variant="outline">
                        <Link href={clipHref(video.url)}><Scissors className="size-3.5" />Clip</Link>
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
