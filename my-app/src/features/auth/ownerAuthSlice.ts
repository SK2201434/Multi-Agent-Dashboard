import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';

import { normalizeApiError, type NormalizedApiError } from '@/api/errors';
import { getCurrentOwner, loginOwner, registerOwner } from '@/features/auth/authApi';
import type {
  OwnerLoginRequest,
  OwnerRegisterRequest,
  OwnerResponse,
} from '@/features/auth/authTypes';
import {
  getOwnerToken,
  removeOwnerToken,
  saveOwnerToken,
} from '@/features/auth/ownerTokenStorage';

type AuthOperation = 'register' | 'login' | 'fetchOwner' | 'restoreSession';

export type AuthState = {
  owner: OwnerResponse | null;
  isAuthenticated: boolean;
  loading: Record<AuthOperation, boolean>;
  errors: Record<AuthOperation, NormalizedApiError | null>;
};

const initialState: AuthState = {
  owner: null,
  isAuthenticated: false,
  loading: { register: false, login: false, fetchOwner: false, restoreSession: false },
  errors: { register: null, login: null, fetchOwner: null, restoreSession: null },
};

// The JWT must never appear in thunk arguments, payloads or state: it only moves between
// the API layer and secure storage inside the thunk bodies below.

/** Login succeeded and the token was saved securely. The token itself is not exposed. */
export type OwnerLoginResult = { tokenSaved: true };

/** `owner` is null when there is no saved session. */
export type OwnerSessionResult = { owner: OwnerResponse | null };

/** Result of `logoutOwnerThunk`; `false` means the saved token could not be removed. */
export type OwnerLogoutResult = { tokenRemoved: boolean };

const createAuthThunk = createAsyncThunk.withTypes<{ rejectValue: NormalizedApiError }>();

const storageError = (message: string): NormalizedApiError => ({ message, category: 'unknown' });

const isUnauthorized = (error: NormalizedApiError) =>
  error.category === 'http' && error.status === 401;

type StoredOwnerResult =
  | { kind: 'owner'; owner: OwnerResponse }
  | { kind: 'no-token' }
  | { kind: 'error'; error: NormalizedApiError };

// Loads the current owner using the saved token. An invalid/expired token (HTTP 401) is
// removed from storage; any other failure (network, timeout, 5xx) keeps it for a retry.
async function loadOwnerWithStoredToken(): Promise<StoredOwnerResult> {
  let token: string | null;
  try {
    token = await getOwnerToken();
  } catch {
    return { kind: 'error', error: storageError('Unable to read the saved session.') };
  }

  if (token === null) return { kind: 'no-token' };

  try {
    return { kind: 'owner', owner: await getCurrentOwner(token) };
  } catch (error) {
    const normalized = normalizeApiError(error);
    if (isUnauthorized(normalized)) {
      try {
        await removeOwnerToken();
      } catch {
        // The in-memory session is cleared regardless; a leftover token is rejected
        // with 401 and removed again on the next restore attempt.
      }
    }
    return { kind: 'error', error: normalized };
  }
}

export const registerOwnerThunk = createAuthThunk<OwnerResponse, OwnerRegisterRequest>(
  'ownerAuth/registerOwner',
  async (data, { rejectWithValue }) => {
    try {
      return await registerOwner(data);
    } catch (error) {
      return rejectWithValue(normalizeApiError(error));
    }
  },
);

// Saves the token securely and resolves without it. The owner becomes authenticated only
// after `fetchCurrentOwnerThunk` succeeds.
export const loginOwnerThunk = createAuthThunk<OwnerLoginResult, OwnerLoginRequest>(
  'ownerAuth/loginOwner',
  async (data, { rejectWithValue }) => {
    let accessToken: string;
    try {
      accessToken = (await loginOwner(data)).access_token;
    } catch (error) {
      return rejectWithValue(normalizeApiError(error));
    }

    try {
      await saveOwnerToken(accessToken);
    } catch {
      return rejectWithValue(
        storageError('Signed in, but the session could not be saved securely on this device.'),
      );
    }

    return { tokenSaved: true };
  },
);

// Fetches the owner using the saved token (e.g. right after login).
export const fetchCurrentOwnerThunk = createAuthThunk<OwnerResponse, void>(
  'ownerAuth/fetchCurrentOwner',
  async (_, { rejectWithValue }) => {
    const result = await loadOwnerWithStoredToken();
    if (result.kind === 'owner') return result.owner;
    if (result.kind === 'no-token') return rejectWithValue(storageError('No saved session found.'));
    return rejectWithValue(result.error);
  },
);

// Restores a session at app start. With no saved token it resolves with `owner: null`
// without making any API request.
export const restoreOwnerSessionThunk = createAuthThunk<OwnerSessionResult, void>(
  'ownerAuth/restoreSession',
  async (_, { rejectWithValue }) => {
    const result = await loadOwnerWithStoredToken();
    if (result.kind === 'owner') return { owner: result.owner };
    if (result.kind === 'no-token') return { owner: null };
    return rejectWithValue(result.error);
  },
);

const ownerAuthSlice = createSlice({
  name: 'ownerAuth',
  initialState,
  reducers: {
    logoutOwner: () => initialState,
  },
  extraReducers: (builder) => {
    // A 401 means the token is no longer valid, so the in-memory session is dropped too.
    // Other failures keep the current session so a temporary outage does not log out.
    const clearSessionIfUnauthorized = (state: AuthState, error: NormalizedApiError) => {
      if (isUnauthorized(error)) {
        state.owner = null;
        state.isAuthenticated = false;
      }
    };

    builder
      // Register: never touches owner/isAuthenticated (no token is issued).
      .addCase(registerOwnerThunk.pending, (state) => {
        state.loading.register = true;
        state.errors.register = null;
      })
      .addCase(registerOwnerThunk.fulfilled, (state) => {
        state.loading.register = false;
      })
      .addCase(registerOwnerThunk.rejected, (state, action) => {
        state.loading.register = false;
        state.errors.register = action.payload ?? normalizeApiError(action.error);
      })
      // Login: the response has no owner, so only loading/error change here.
      // A failed login does not clear an existing session.
      .addCase(loginOwnerThunk.pending, (state) => {
        state.loading.login = true;
        state.errors.login = null;
      })
      .addCase(loginOwnerThunk.fulfilled, (state) => {
        state.loading.login = false;
      })
      .addCase(loginOwnerThunk.rejected, (state, action) => {
        state.loading.login = false;
        state.errors.login = action.payload ?? normalizeApiError(action.error);
      })
      // Fetch current owner: authenticates on success.
      .addCase(fetchCurrentOwnerThunk.pending, (state) => {
        state.loading.fetchOwner = true;
        state.errors.fetchOwner = null;
      })
      .addCase(fetchCurrentOwnerThunk.fulfilled, (state, action) => {
        state.loading.fetchOwner = false;
        state.owner = action.payload;
        state.isAuthenticated = true;
      })
      .addCase(fetchCurrentOwnerThunk.rejected, (state, action) => {
        const error = action.payload ?? normalizeApiError(action.error);
        state.loading.fetchOwner = false;
        state.errors.fetchOwner = error;
        clearSessionIfUnauthorized(state, error);
      })
      // Restore session: no saved token resolves as unauthenticated.
      .addCase(restoreOwnerSessionThunk.pending, (state) => {
        state.loading.restoreSession = true;
        state.errors.restoreSession = null;
      })
      .addCase(restoreOwnerSessionThunk.fulfilled, (state, action) => {
        state.loading.restoreSession = false;
        state.owner = action.payload.owner;
        state.isAuthenticated = action.payload.owner !== null;
      })
      .addCase(restoreOwnerSessionThunk.rejected, (state, action) => {
        const error = action.payload ?? normalizeApiError(action.error);
        state.loading.restoreSession = false;
        state.errors.restoreSession = error;
        clearSessionIfUnauthorized(state, error);
      });
  },
});

const ownerAuthReducer = ownerAuthSlice.reducer;

export const { logoutOwner } = ownerAuthSlice.actions;
export default ownerAuthReducer;

// Clears the in-memory session first, so the owner is logged out even if deleting the
// saved token fails; the result reports whether the token was actually removed.
export const logoutOwnerThunk = createAuthThunk<OwnerLogoutResult, void>(
  'ownerAuth/logout',
  async (_, { dispatch }) => {
    dispatch(logoutOwner());
    try {
      await removeOwnerToken();
      return { tokenRemoved: true };
    } catch {
      return { tokenRemoved: false };
    }
  },
);
