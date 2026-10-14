import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

const OWNER_TOKEN_KEY = 'owner_access_token';

// expo-secure-store has no web implementation, and no insecure fallback (e.g. localStorage)
// is provided on purpose. On web, nothing is ever stored: saving throws, reading returns
// null, and removing is a no-op.
const isSecureStorageAvailable = Platform.OS !== 'web';

export async function saveOwnerToken(token: string): Promise<void> {
  if (token.trim() === '') {
    throw new Error('Cannot save an empty owner access token.');
  }
  if (!isSecureStorageAvailable) {
    throw new Error('Secure token storage is not available on web.');
  }
  await SecureStore.setItemAsync(OWNER_TOKEN_KEY, token);
}

export async function getOwnerToken(): Promise<string | null> {
  if (!isSecureStorageAvailable) return null;
  return SecureStore.getItemAsync(OWNER_TOKEN_KEY);
}

export async function removeOwnerToken(): Promise<void> {
  if (!isSecureStorageAvailable) return;
  await SecureStore.deleteItemAsync(OWNER_TOKEN_KEY);
}
