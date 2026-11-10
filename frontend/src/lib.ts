import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "./api";
import type { AiStatus, App, JobsPayload, Status, TrackingInfo } from "./types";

export const STATUS_LABEL: Record<Status, string> = {
  interested: "Kiinnostava", drafting: "Työn alla", applied: "Haettu", interview: "Haastattelu",
  offer: "Tarjous", rejected: "Hylätty", no_response: "Ei vastausta", withdrawn: "Peruttu",
};
export const STATUS_ORDER: Status[] = ["interested", "drafting", "applied", "interview", "offer", "rejected", "no_response", "withdrawn"];

// ------------------------------------------------------------ data

export const useJobs = () => useQuery({ queryKey: ["jobs"], queryFn: () => get<JobsPayload>("/api/jobs"), staleTime: 60_000 });
export const useApps = () =>
  useQuery({ queryKey: ["apps"], queryFn: () => get<{ apps: App[] }>("/api/apps"), staleTime: 10_000 });
export const useTracking = () =>
  useQuery({ queryKey: ["tracking"], queryFn: () => get<TrackingInfo>("/api/tracking"), staleTime: 30_000 });
export interface ProjectSummary {
  open: number; seen_at: string; generated?: string; scan: { running: boolean };
  new: { id: string; repo: string; kind: string; label: string; title: string; found: string }[];
}
/** Projektiskannerin uudet ehdotukset (löydetty viimeisen katselun jälkeen): sivupalkki ja etusivu. */
export const useProjectSummary = () =>
  useQuery({ queryKey: ["project-summary"], queryFn: () => get<ProjectSummary>("/api/projects/summary"),
    refetchInterval: (q) => (q.state.data?.scan.running ? 5_000 : 300_000), staleTime: 30_000 });
export const useAiStatus = () =>
  useQuery({ queryKey: ["ai-status"], queryFn: () => get<AiStatus>("/api/ai/status"), refetchInterval: 30_000, staleTime: 10_000 });

// ------------------------------------------------------------ muotoilu

const FI_DAYS = ["sunnuntai", "maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai"];
const FI_MONTHS = ["tammikuuta", "helmikuuta", "maaliskuuta", "huhtikuuta", "toukokuuta", "kesäkuuta", "heinäkuuta",
  "elokuuta", "syyskuuta", "lokakuuta", "marraskuuta", "joulukuuta"];

export function longDate(d = new Date()) {
  const day = FI_DAYS[d.getDay()];
  return `${day[0].toUpperCase()}${day.slice(1)} ${d.getDate()}. ${FI_MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

/** 2026-11-03 -> 3.11. (tai 3.11.2025 jos eri vuosi) */
export function shortDate(iso?: string | null) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y) return "";
  return y === new Date().getFullYear() ? `${d}.${m}.` : `${d}.${m}.${y}`;
}

export function daysUntil(iso?: string | null) {
  if (!iso) return null;
  const t = new Date(iso.slice(0, 10) + "T00:00:00").getTime();
  const now = new Date(); now.setHours(0, 0, 0, 0);
  return Math.round((t - now.getTime()) / 86_400_000);
}

export function daysAgo(iso?: string | null) {
  const d = daysUntil(iso);
  return d === null ? null : -d;
}

export function fmtNum(n: number | null | undefined, digits = 0) {
  return n === null || n === undefined ? "–" : n.toLocaleString("fi-FI", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

// ------------------------------------------------------------ näppäimet

/** Näppäinoikotiet; ohitetaan kun kirjoitetaan kenttään tai modaali on auki (paitsi Escape). */
export function useKeys(map: Record<string, (e: KeyboardEvent) => void>, active = true) {
  const ref = useRef(map);
  ref.current = map;
  useEffect(() => {
    if (!active) return;
    const on = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target as HTMLElement;
      const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
      if (typing && e.key !== "Escape") return;
      if (document.querySelector("dialog[open]") && e.key !== "Escape") return;
      const fn = ref.current[e.key];
      if (fn) { e.preventDefault(); fn(e); }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [active]);
}

export function useTitle(t: string) {
  useEffect(() => { document.title = t ? `${t} · Työhaku` : "Työhaku"; }, [t]);
}

export function safeStorage<T>(key: string, fallback: T): [() => T, (v: T) => void] {
  return [
    () => { try { const v = sessionStorage.getItem(key); return v ? (JSON.parse(v) as T) : fallback; } catch { return fallback; } },
    (v: T) => { try { sessionStorage.setItem(key, JSON.stringify(v)); } catch { /* yksityinen tila */ } },
  ];
}
