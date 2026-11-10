import { useMutation, useQueryClient } from "@tanstack/react-query";
import { post } from "./api";
import { STATUS_LABEL } from "./lib";
import type { App, Status } from "./types";

/** Seurannan tilan vaihto ilmoitukselle: luo hakemuksen tarvittaessa, muuten siirtää tilaa (backend tarkistaa siirtymän). */
export function useSetStatus(toast: (m: string) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ url, status, apps }: { url: string; status: Status; apps: App[] }) => {
      const a = apps.find((x) => x.url === url);
      if (!a) return post<{ app: App }>("/api/apps", { action: "create", job_url: url, fields: { status } });
      return post<{ app: App }>("/api/apps", { action: "update", id: a.id, fields: { status } });
    },
    onSuccess: (r) => {
      toast(`Tila: ${STATUS_LABEL[r.app.status]}`);
      qc.invalidateQueries({ queryKey: ["apps"] });
      qc.invalidateQueries({ queryKey: ["tracking"] }); qc.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (e: Error) => toast(e.message),
  });
}

export function useUpdateApp(toast: (m: string) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, fields }: { id: string; fields: Partial<App> }) => post<{ app: App }>("/api/apps", { action: "update", id, fields }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["apps"] }); qc.invalidateQueries({ queryKey: ["tracking"] }); qc.invalidateQueries({ queryKey: ["stats"] }); },
    onError: (e: Error) => toast(e.message),
  });
}

/** Tallennettu / piilotettu -merkintä (vanha state.json). */
export function useJobState(toast: (m: string) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { url: string; saved?: boolean; hidden?: boolean }) => post("/api/state", b),
    onSuccess: (_r, b) => {
      toast(b.hidden ? "Piilotettu" : b.hidden === false ? "Palautettu" : b.saved ? "Tallennettu" : "Tallennus poistettu");
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e: Error) => toast(e.message),
  });
}

export async function copy(text: string, toast: (m: string) => void) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Kopioitu leikepöydälle");
  } catch {
    toast("Kopiointi ei onnistunut: valitse teksti ja paina Ctrl+C");
  }
}
