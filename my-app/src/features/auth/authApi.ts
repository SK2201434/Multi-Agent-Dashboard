import apiClient from '@/api/client';
import { ENDPOINTS } from '@/api/endpoints';
import type {
  OwnerLoginRequest,
  OwnerRegisterRequest,
  OwnerResponse,
  TokenResponse,
} from '@/features/auth/authTypes';

// Errors are intentionally left as raw Axios errors; callers use `normalizeApiError`.

export async function registerOwner(data: OwnerRegisterRequest): Promise<OwnerResponse> {
  const response = await apiClient.post<OwnerResponse>(ENDPOINTS.auth.register, data);
  return response.data;
}

export async function loginOwner(data: OwnerLoginRequest): Promise<TokenResponse> {
  const response = await apiClient.post<TokenResponse>(ENDPOINTS.auth.login, data);
  return response.data;
}

export async function getCurrentOwner(token: string): Promise<OwnerResponse> {
  const response = await apiClient.get<OwnerResponse>(ENDPOINTS.auth.me, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return response.data;
}
