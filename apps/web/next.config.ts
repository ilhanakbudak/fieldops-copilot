import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // The FastAPI service owns retrieval and the connectors. Proxying keeps it
  // same-origin in the browser, so the session cookie works without CORS.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_URL ?? "http://localhost:8000"}/:path*`,
      },
    ];
  },
};

export default config;
