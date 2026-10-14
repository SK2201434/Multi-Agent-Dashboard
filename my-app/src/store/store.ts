import { configureStore } from '@reduxjs/toolkit';

import ownerAuthReducer from '@/features/auth/ownerAuthSlice';

export const store = configureStore({
  // Register feature slices here.
  reducer: {
    ownerAuth: ownerAuthReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
