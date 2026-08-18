import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hujjat to'ldirish tizimi",
  description: "Pasport, ID karta va diplomdan ma'lumot olib, hujjatlarni avtomatik to'ldirish",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="uz">
      <body className="min-h-screen">
        <header className="border-b border-stone-200 bg-white">
          <div className="mx-auto max-w-5xl px-4 py-3">
            <a href="/" className="font-semibold">Hujjat to‘ldirish tizimi</a>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
