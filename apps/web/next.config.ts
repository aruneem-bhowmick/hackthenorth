import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs/config";

const nextConfig: NextConfig = {
  /* config options here */
};

export default withSentryConfig(nextConfig, {
  silent: true,
  // Source-map upload needs an org/project + auth token we don't have yet
  // (P4 deploy concern); disabling keeps local dev builds clean.
  sourcemaps: { disable: true },
});
