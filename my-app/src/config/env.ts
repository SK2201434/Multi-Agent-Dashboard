function parseApiUrl(value: string | undefined): string {
  if (!value) {
    throw new Error(
      'Missing EXPO_PUBLIC_API_URL. Set it in the root .env file and restart the dev server.',
    );
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(
      `Invalid EXPO_PUBLIC_API_URL "${value}". Expected an absolute http(s) URL, e.g. http://<host>:<port>.`,
    );
  }

  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(
      `Invalid EXPO_PUBLIC_API_URL "${value}". Only http and https URLs are supported.`,
    );
  }

  return value.replace(/\/+$/, '');
}

// Must be referenced as a literal `process.env.EXPO_PUBLIC_*` so Expo can inline it at build time.
export const API_BASE_URL: string = parseApiUrl(process.env.EXPO_PUBLIC_API_URL);
