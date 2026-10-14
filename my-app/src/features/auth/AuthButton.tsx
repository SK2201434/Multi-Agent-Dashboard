import { ActivityIndicator, Pressable, StyleSheet } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';

type AuthButtonProps = { label: string; onPress: () => void; isLoading?: boolean };

export function AuthButton({ label, onPress, isLoading = false }: AuthButtonProps) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: isLoading, busy: isLoading }}
      disabled={isLoading}
      onPress={onPress}
      style={[styles.button, isLoading && styles.disabled]}>
      {isLoading ? <ActivityIndicator color="#ffffff" /> : <ThemedText style={styles.label}>{label}</ThemedText>}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    minHeight: 48,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: Spacing.three,
    backgroundColor: '#208AEF',
  },
  disabled: { opacity: 0.7 },
  label: { color: '#ffffff', fontWeight: '600' },
});
