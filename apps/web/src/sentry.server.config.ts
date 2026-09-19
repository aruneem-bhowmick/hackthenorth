import * as Sentry from "@sentry/nextjs";

if (process.env.SENTRY_DSN_WEB) {
  Sentry.init({
    dsn: process.env.SENTRY_DSN_WEB,
    environment: process.env.SENTRY_ENVIRONMENT ?? "development",
    tracesSampleRate: 1.0,
  });
}
