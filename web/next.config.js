/** @type {import('next').NextConfig} */
const nextConfig = {
  // The browser talks to /api/*; this proxies it to the FastAPI server. Next bakes the
  // destination in at build time, so set API_INTERNAL_URL when building (e.g. http://api:8000
  // in docker-compose).
  async rewrites() {
    const target = process.env.API_INTERNAL_URL || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${target}/:path*` }];
  },
};

module.exports = nextConfig;
