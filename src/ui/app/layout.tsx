import type { Metadata } from "next";
import { ThemeProvider } from "@kui/foundations-react-external";
import "./globals.css";

export const metadata: Metadata = {
  title: "VyapaarOS | Razorpay Buildathon",
  description: "VyapaarOS - Agentic Commerce Protocol",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className="nv-dark"
      style={{ backgroundColor: "#0c0c0c" }}
      suppressHydrationWarning
    >
      <body style={{ backgroundColor: "var(--background-color-surface-base)" }}>
        <ThemeProvider theme="dark" density="standard" global target="html">
          {children}
        </ThemeProvider>
        <script src="https://checkout.razorpay.com/v1/checkout.js" async></script>
      </body>
    </html>
  );
}
