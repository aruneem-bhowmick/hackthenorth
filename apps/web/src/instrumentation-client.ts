import * as Sentry from "@sentry/nextjs";

// NFR-OBS-001 covers frontend -> API -> worker -> investigator, but the P0
// exit gate only checks the API -> worker trace. Wiring the browser SDK now
// is cheap and avoids a later retrofit.
if (process.env.NEXT_PUBLIC_SENTRY_DSN) {
  Sentry.init({
    dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "development",
    tracesSampleRate: 1.0,
  });
}

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
