// Käynnissä olevien palvelinkutsujen laskuri (erillään, jotta api.ts ei tuo React-komponentteja: ei kehäriippuvuutta).
let inflight = 0;
const listeners = new Set<(n: number) => void>();
const emit = () => listeners.forEach((f) => f(inflight));

export function track<T>(p: Promise<T>): Promise<T> {
  inflight++;
  emit();
  return p.finally(() => { inflight = Math.max(0, inflight - 1); emit(); });
}

export function subscribe(f: (n: number) => void): () => void {
  listeners.add(f);
  return () => { listeners.delete(f); };
}
