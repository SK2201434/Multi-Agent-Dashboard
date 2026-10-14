// Owner authentication contracts (backend/app/schemas/auth.py).
// UUIDs are strings in JSON. Tenant authentication has its own, separate contracts.

/** POST /api/v1/auth/register */
export type OwnerRegisterRequest = {
  /** 2–100 characters. */
  full_name: string;
  email: string;
  /** 8–128 characters. */
  password: string;
};

/** POST /api/v1/auth/login */
export type OwnerLoginRequest = {
  email: string;
  password: string;
};

/** Response of POST /api/v1/auth/register (201) and GET /api/v1/auth/me. */
export type OwnerResponse = {
  /** UUID. */
  id: string;
  full_name: string;
  email: string;
  is_active: boolean;
};

/** Response of POST /api/v1/auth/login. The backend sends `"bearer"` by default. */
export type TokenResponse = {
  access_token: string;
  token_type: string;
};
