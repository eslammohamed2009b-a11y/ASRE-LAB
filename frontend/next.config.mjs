const fastapiApiUrl = process.env.NEXT_PUBLIC_FASTAPI_API_URL?.replace(/\/+$/, "");

/** @type {import("next").NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return fastapiApiUrl ? [{ source: "/_asre-api/:path*", destination: `${fastapiApiUrl}/:path*` }] : [];
  },
};

export default nextConfig;
