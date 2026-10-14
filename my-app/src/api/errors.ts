import { isAxiosError } from 'axios';

export type ApiErrorCategory = 'network' | 'timeout' | 'http' | 'unknown';

export type NormalizedApiError = {
  message: string;
  category: ApiErrorCategory;
  status?: number;
  /** Original FastAPI validation details (`detail` array), left untouched. */
  details?: unknown;
};

const TIMEOUT_CODES = new Set(['ECONNABORTED', 'ETIMEDOUT']);

const NETWORK_MESSAGE = 'Unable to reach the server. Check your connection and try again.';
const TIMEOUT_MESSAGE = 'The request timed out. Please try again.';
const UNKNOWN_MESSAGE = 'Something went wrong. Please try again.';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : undefined;
}

// Formats one FastAPI validation item, e.g. {loc: ['body', 'email'], msg: '...'} -> "email: ...".
function formatValidationItem(item: unknown): string | undefined {
  if (!isRecord(item)) return nonEmptyString(item);

  const msg = nonEmptyString(item.msg);
  if (!msg) return undefined;

  const field = Array.isArray(item.loc)
    ? item.loc
        .filter(
          (part): part is string | number => typeof part === 'string' || typeof part === 'number',
        )
        .filter((part) => part !== 'body' && part !== 'query' && part !== 'path')
        .join('.')
    : '';

  return field ? `${field}: ${msg}` : msg;
}

function extractMessage(data: unknown): string | undefined {
  const direct = nonEmptyString(data);
  if (direct !== undefined) return direct;
  if (!isRecord(data)) return undefined;

  const { detail } = data;
  const detailText = nonEmptyString(detail);
  if (detailText) return detailText;

  if (Array.isArray(detail)) {
    const parts = detail
      .map(formatValidationItem)
      .filter((part): part is string => part !== undefined);
    if (parts.length > 0) return parts.join('; ');
  }

  return nonEmptyString(data.message);
}

function httpFallbackMessage(status: number): string {
  if (status === 401) return 'Your session is invalid or has expired. Please sign in again.';
  if (status === 403) return 'You do not have permission to perform this action.';
  if (status === 404) return 'The requested resource was not found.';
  if (status >= 500) return 'The server encountered an error. Please try again later.';
  return `The request failed (HTTP ${status}).`;
}

/**
 * Converts any thrown value into a {@link NormalizedApiError}. Never throws.
 * Only the response status and body are read; request config and headers are never exposed.
 */
export function normalizeApiError(error: unknown): NormalizedApiError {
  try {
    if (!isAxiosError(error)) {
      return { message: UNKNOWN_MESSAGE, category: 'unknown' };
    }

    const { response } = error;
    if (response) {
      const { status, data } = response;
      const details = isRecord(data) && Array.isArray(data.detail) ? data.detail : undefined;
      return {
        message: extractMessage(data) ?? httpFallbackMessage(status),
        category: 'http',
        status,
        ...(details !== undefined && { details }),
      };
    }

    if (error.code !== undefined && TIMEOUT_CODES.has(error.code)) {
      return { message: TIMEOUT_MESSAGE, category: 'timeout' };
    }

    // Cancelled requests are not connectivity problems.
    if (error.code === 'ERR_CANCELED') {
      return { message: UNKNOWN_MESSAGE, category: 'unknown' };
    }

    return { message: NETWORK_MESSAGE, category: 'network' };
  } catch {
    return { message: UNKNOWN_MESSAGE, category: 'unknown' };
  }
}
