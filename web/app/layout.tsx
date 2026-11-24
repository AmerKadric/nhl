import type { Metadata, Viewport } from "next";
import { Barlow_Condensed, IBM_Plex_Mono, Inter } from "next/font/google";

import { Suspense } from "react";

import { Footer, Header, StaleBanner } from "@/components/Chrome";

import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"], display: "swap" });
const barlow = Barlow_Condensed({ variable: "--font-barlow", subsets: ["latin"], weight: ["500", "600", "700"], display: "swap" });
const plex = IBM_Plex_Mono({ variable: "--font-plex-mono", subsets: ["latin"], weight: ["400", "500", "600"], display: "swap" });

export const metadata: Metadata = {
  title: { default: "PUCKIQ — NHL score forecasts", template: "%s · PUCKIQ" },
  description: "Know the numbers. Before the puck drops. Data-driven NHL final-score forecasts with a public track record.",
};

export const viewport: Viewport = { themeColor: "#090b0f", colorScheme: "dark" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} ${barlow.variable} ${plex.variable} h-full`}>
      <body className="flex min-h-full flex-col">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-md focus:bg-raised focus:px-3 focus:py-2">
          Skip to content
        </a>
        <Header />
        <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 pb-8 pt-6 sm:px-6">
          <Suspense fallback={null}>
            <StaleBanner />
          </Suspense>
          {children}
        </main>
        <Footer />
      </body>
    </html>
  );
}
