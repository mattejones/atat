import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "ATAT — Application Tracking & Automation Tool",
  description: "LLM-powered CV generation and job application tracking.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen bg-bg-base text-text-primary antialiased flex flex-col">
        <Nav />
        <main className="flex-1 max-w-6xl w-full mx-auto px-6 py-8">{children}</main>
        <Footer />
      </body>
    </html>
  );
}
