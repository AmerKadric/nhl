"use client";

import { useSyncExternalStore } from "react";

import { timeET } from "@/lib/format";

const subscribe = () => () => {};

/** Puck-drop time in the visitor's own time zone.
 *
 * The server cannot know the visitor's zone, so the first paint shows US
 * Eastern time (the league's schedule clock); once the page is interactive
 * it switches to local time with the zone named, so there is no ambiguity. */
export function LocalTime({ iso }: { iso: string | null | undefined }) {
  const hydrated = useSyncExternalStore(subscribe, () => true, () => false);
  if (!iso) return <span>TBD</span>;
  const text = hydrated
    ? new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(new Date(iso))
    : timeET(iso);
  return (
    <time dateTime={iso} suppressHydrationWarning>
      {text}
    </time>
  );
}
