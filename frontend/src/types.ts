export interface Job {
  title: string;
  url: string;
  company: string;
  location: string;
  source: string;
  text?: string;
  detail?: { posted?: string; deadline?: string; employment?: string; places?: string[]; salary?: string };
}

export interface Row {
  score: number;
  status: "ok" | "expired" | "excluded";
  reasons: string[];
  area?: string;
  deadline?: string;
  watch?: boolean;
  first_seen?: string;
  parts?: Record<string, number>;
  score_keyword?: number;
  score_semantic?: number | null;
  score_hybrid?: number;
  score_llm?: number | null;
  score_combined?: number | null;
  llm?: { fit: number; level: "junior" | "mid" | "senior"; reason: string } | null;
  job: Job;
}

export interface JobState { saved?: boolean; hidden?: boolean }

export interface JobsPayload {
  updated: string | null;
  demo?: boolean;
  partial?: boolean;
  rows: Row[];
  state: Record<string, JobState>;
  gone: Record<string, string>;
}

export type Status =
  | "interested" | "drafting" | "applied" | "interview" | "offer" | "rejected" | "no_response" | "withdrawn";

export interface App {
  id: string;
  status: Status;
  title: string;
  company: string;
  location: string;
  url: string;
  source: string;
  notes: string;
  deadline: string;
  applied_date: string;
  next_date: string;
  next_step: string;
  contact_name: string;
  contact_email: string;
  salary: string;
  created: string;
  updated: string;
  score: number | null;
  history: { status: Status; at: string }[];
}

export interface Reminder {
  id: string; kind: "follow_up" | "next_step" | "deadline"; date: string; text: string;
  title: string; company: string; url: string; days?: number;
}

export interface TrackingInfo {
  statuses: Status[];
  labels: Record<Status, string>;
  final: Status[];
  transitions: Record<Status, Status[]>;
  reminders: Reminder[];
  follow_up_days: number;
}

export interface Ref {
  id: string; type: "ad" | "profile"; text: string;
  start?: number | null; end?: number | null; source_ref?: string; kind?: string;
}

export interface Explanation {
  strengths: { text: string; ad: string[]; profile: string[] }[];
  gaps: { text: string; ad: string[]; severity: "must" | "nice" }[];
  dropped: { text: string; reason: string }[];
  verdict: "hyvä" | "kohtalainen" | "heikko";
  summary: string;
  refs: Record<string, Ref>;
  cached: boolean;
  model: string;
  created?: string;
}

export interface Claim {
  text: string; start: number; end: number;
  status: "ok" | "check" | "flag"; kind: "fact" | "motivation";
  reasons: string[]; first_person: boolean;
  source: (Ref & { sim: number }) | null;
}

export interface Section {
  key: string; title: string; text: string; edited: boolean; style?: string[];
  verify: { claims: Claim[]; flagged: number; check: number };
}

export interface Draft {
  url: string; title: string; company: string; lang: "fi" | "en"; model: string;
  created: string; updated: string; sections: Section[]; history: { at: string; sections: Section[] }[];
}

export interface Source {
  n: number; job_id: string; title: string; company: string; city: string; url: string; posted: string;
  sim: number; chunks: { id: number; text: string; why: string }[];
}

export interface AiStatus {
  llm: { provider: "ollama" | "claude"; model: string; writing_model: string; reachable?: boolean;
         model_available?: boolean; models?: string[]; key_set?: boolean };
  index: { exists: boolean; active: string; active_ok?: boolean; jobs?: number;
           configs: { config_id: string; model: string; chunk_size: number; chunk_overlap: number; jobs: number; created: string }[] };
  scoring: { mode: "keyword" | "semantic" | "hybrid"; w_keyword: number; w_semantic: number; method: string; aggregation: string };
  embed: { backend: string; model: string; chunk_size: number; chunk_overlap: number };
  remote?: boolean;   // käytössä Tailscalen kautta: asetukset ja avaimet vain koneen ääressä
}

export interface EvalRun {
  at: string; note: string; n_labels: number; n_relevant: number;
  config: { config_id: string; w_keyword: number; w_semantic: number; hybrid_method: string; aggregation: string; agg_k: number };
  rankers: Record<string, { "p@k": number; "ndcg@k": number; p_ci?: number[]; ndcg_ci?: number[];
                            "full_ndcg@k"?: number; full_coverage?: number }>;
  sweep?: { w_semantic: number; "p@k": number; "ndcg@k": number }[];
}
