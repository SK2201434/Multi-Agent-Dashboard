import type { NormalizedApiError } from '@/api/errors';

function isNormalizedApiError(error: unknown): error is NormalizedApiError {
  return (
    typeof error === 'object' &&
    error !== null &&
    'message' in error &&
    typeof error.message === 'string' &&
    'category' in error
  );
}

// Thunks reject with a NormalizedApiError (see `rejectWithValue`), which `.unwrap()` throws.
export function getAuthErrorMessage(error: unknown): string {
  return isNormalizedApiError(error) ? error.message : 'Something went wrong. Please try again.';
}
