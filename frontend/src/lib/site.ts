export const SITE_NAME = "Katakata";
export const DEFAULT_SITE_URL = "https://www.supoclip.com";
export const HOSTED_APP_URL = DEFAULT_SITE_URL;
// AGPL-3.0: users of the hosted app must be able to get this modified source.
export const GITHUB_URL = "https://github.com/kingzion24/supoclip";
// Katakata is based on SupoClip (https://github.com/FujiwaraChoki/supoclip).
export const UPSTREAM_GITHUB_URL = "https://github.com/FujiwaraChoki/supoclip";
// The App Store badge, Smart App Banner and app structured data only appear
// when NEXT_PUBLIC_APP_STORE_ID names your own iOS app.
export const APP_STORE_ID = process.env.NEXT_PUBLIC_APP_STORE_ID || "";
export const APP_STORE_URL = APP_STORE_ID ? `https://apps.apple.com/app/id${APP_STORE_ID}` : null;

export function getSiteUrl() {
  try {
    return new URL(process.env.NEXT_PUBLIC_APP_URL || DEFAULT_SITE_URL).origin;
  } catch {
    return DEFAULT_SITE_URL;
  }
}
