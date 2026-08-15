import type { NextConfig } from "next";

const API_TARGET =
  process.env.AGENT_API_URL ?? "http://127.0.0.1:8420";

const nextConfig: NextConfig = {
  // API-ul agentului (serverul Python din app/server.py) e proxiat prin
  // acelasi port ca frontend-ul, deci browserul vede o singura origine -
  // nu exista probleme CORS si functioneaza si de pe telefon, pe WiFi.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_TARGET}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
