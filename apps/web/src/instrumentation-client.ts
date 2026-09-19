import * as Sentry from "@sentry/nextjs";

// NFR-OBS-001 covers frontend -> API -> worker -> investigator. The P0 exit
// gate only required the API -> worker leg, but without tracePropagationTargets
// the browser SDK won't attach sentry-trace/baggage headers to cross-origin
// fetch calls (localhost:3000 -> localhost:8000 counts as cross-origin), so
// the pageload/click trace and the API's trace never actually link up even
// though both are being sent correctly on their own.
const apiOrigin = new URL(
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).origin;

if (process.env.NEXT_PUBLIC_SENTRY_DSN) {
  Sentry.init({
    dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "development",
    tracesSampleRate: 1.0,
    tracePropagationTargets: [apiOrigin],
    // NFR-PRIV-003: review pages can render an entire brief and source text.
    // Mask text, inputs, and media explicitly rather than relying on SDK
    // defaults before Session Replay is enabled in production.
    integrations: [Sentry.replayIntegration({
      maskAllText: true,
      maskAllInputs: true,
      blockAllMedia: true,
    })],
    replaysSessionSampleRate: 0.05,
    replaysOnErrorSampleRate: 1.0,
  });
}

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
