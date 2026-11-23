"use client";

import { useEffect } from "react";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <div className="panel hatch mx-auto mt-10 max-w-xl px-6 py-8 text-center" role="alert">
      <div className="eyebrow text-bad">Data unavailable</div>
      <h1 className="display mt-2 text-3xl font-bold tracking-wide">The prediction service did not respond</h1>
      <p className="mt-3 text-sm text-ink-2">
        PUCKIQ would rather show nothing than show numbers it cannot stand behind. Forecasts will reappear as soon as the service is reachable.
      </p>
      <button type="button" onClick={reset} className="mt-5 rounded-lg border border-line bg-raised px-4 py-2 text-sm font-medium text-ink transition-colors hover:border-ice/40 active:scale-95">
        Try again
      </button>
    </div>
  );
}
