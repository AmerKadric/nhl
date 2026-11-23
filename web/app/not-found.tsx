import Link from "next/link";

export default function NotFound() {
  return (
    <div className="panel mx-auto mt-10 max-w-xl px-6 py-8 text-center">
      <div className="eyebrow">404</div>
      <h1 className="display mt-2 text-3xl font-bold tracking-wide">Nothing at this address</h1>
      <p className="mt-3 text-sm text-ink-2">That game or team does not exist in PUCKIQ&apos;s data.</p>
      <Link href="/" className="mt-5 inline-block rounded-lg border border-line bg-raised px-4 py-2 text-sm font-medium text-ink transition-colors hover:border-ice/40 active:scale-95">
        Back to today&apos;s games
      </Link>
    </div>
  );
}
