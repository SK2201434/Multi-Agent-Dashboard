import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import {
  selectAuthErrors,
  selectAuthLoading,
  selectIsAuthenticated,
  selectOwner,
} from '@/features/auth/ownerAuthSelectors';
import { OwnerLoginScreen } from '@/features/auth/OwnerLoginScreen';
import { restoreOwnerSessionThunk } from '@/features/auth/ownerAuthSlice';
import { OwnerRegisterScreen } from '@/features/auth/OwnerRegisterScreen';
import { useAppDispatch, useAppSelector } from '@/store/hooks';

type OwnerAuthGateProps = { children: ReactNode };

// Restores a saved owner session once at startup and renders `children` (the protected
// owner navigation) only for an authenticated owner. Render it inside the Redux Provider.
export function OwnerAuthGate({ children }: OwnerAuthGateProps) {
  const dispatch = useAppDispatch();
  const owner = useAppSelector(selectOwner);
  const isAuthenticated = useAppSelector(selectIsAuthenticated);
  const { restoreSession: isRestoring } = useAppSelector(selectAuthLoading);
  const { restoreSession: restoreError } = useAppSelector(selectAuthErrors);

  // The thunk's loading flag is false until it is dispatched, so track the first attempt
  // to avoid flashing the signed-out screen before restoration starts.
  const [hasAttempted, setHasAttempted] = useState(false);
  // Refs survive Strict Mode's dev-only effect re-run, so the request is sent once.
  const hasStarted = useRef(false);
  // Which signed-out screen is shown. Local UI state, not part of the auth state.
  const [authScreen, setAuthScreen] = useState<'login' | 'register'>('login');

  useEffect(() => {
    if (hasStarted.current) return;
    hasStarted.current = true;
    dispatch(restoreOwnerSessionThunk()).then(() => setHasAttempted(true));
  }, [dispatch]);

  if (!hasAttempted || isRestoring) {
    return (
      <ThemedView style={styles.container}>
        <ActivityIndicator />
      </ThemedView>
    );
  }

  if (isAuthenticated && owner) {
    return children;
  }

  // Failures other than 401 (network, timeout, server, storage) keep any saved token,
  // so the user can retry. A 401 means the token was invalid and is already cleared.
  if (restoreError && !(restoreError.category === 'http' && restoreError.status === 401)) {
    return (
      <ThemedView style={styles.container}>
        <ThemedText type="subtitle" style={styles.text}>
          Couldn&apos;t restore your session
        </ThemedText>
        <ThemedText style={styles.text}>{restoreError.message}</ThemedText>
        <Pressable
          accessibilityRole="button"
          onPress={() => dispatch(restoreOwnerSessionThunk())}>
          <ThemedText type="linkPrimary">Retry</ThemedText>
        </Pressable>
      </ThemedView>
    );
  }

  return authScreen === 'register' ? (
    <OwnerRegisterScreen onShowLogin={() => setAuthScreen('login')} />
  ) : (
    <OwnerLoginScreen onShowRegister={() => setAuthScreen('register')} />
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.three,
    padding: Spacing.four,
  },
  text: {
    textAlign: 'center',
  },
});
