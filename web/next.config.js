/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output keeps the Cloud Run image small (infra/deploy.sh §1 ATLAS).
  output: "standalone",
  reactStrictMode: true,
  eslint: {
    // CI (ATLAS WO5) runs lint separately; don't fail `next build` on lint.
    ignoreDuringBuilds: true,
  },
};

module.exports = nextConfig;
