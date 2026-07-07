import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/contexts/ThemeContext";

export const metadata: Metadata = {
  title: "IG → FB Reposter",
  description: "Multi-fanpage Instagram to Facebook auto reposter",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        {/* Designer canvas fonts — a <link> always applies, unlike an @import
            placed after the @tailwind directives in globals.css */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700;900&family=Montserrat:wght@400;600;700;900&display=swap"
        />
      </head>
      <body className="bg-bg-default text-text-primary antialiased">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
