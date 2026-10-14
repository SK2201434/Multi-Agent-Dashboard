import type { RootState } from '@/store/store';

export const selectOwner = (state: RootState) => state.ownerAuth.owner;
export const selectIsAuthenticated = (state: RootState) => state.ownerAuth.isAuthenticated;
export const selectAuthLoading = (state: RootState) => state.ownerAuth.loading;
export const selectAuthErrors = (state: RootState) => state.ownerAuth.errors;
