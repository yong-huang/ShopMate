import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server (server.js) for the Docker image
  // (frontend/Dockerfile.web, System Design section 3.10).
  output: "standalone",
};

export default nextConfig;
