import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import DemoBanner from "@/components/DemoBanner";
import AuthGate from "@/components/AuthGate";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "ATAT — Application Tracking & Automation Tool",
  description: "LLM-powered CV generation and job application tracking.",
  appleWebApp: { capable: true, title: "ATAT", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover", // lets the bottom tab bar sit above the iOS home indicator
  themeColor: "#F0EDE8",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen bg-bg-base text-text-primary antialiased flex flex-col">
        <Nav />
        <DemoBanner />
        {/* pb leaves room for the fixed bottom tab bar (and the iOS home indicator) on phones */}
        <main className="flex-1 max-w-6xl w-full mx-auto px-4 sm:px-6 py-6 sm:py-8 pb-[calc(5rem+env(safe-area-inset-bottom))] md:pb-8">
          <AuthGate>{children}</AuthGate>
        </main>
        <Footer />
      </body>
    </html>
  );
}
