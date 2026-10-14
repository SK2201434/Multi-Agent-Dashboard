import { useState } from 'react';
import {
  Controller,
  type Control,
  type FieldValues,
  type Path,
} from 'react-hook-form';
import { Pressable, StyleSheet, TextInput, View, type TextInputProps } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

type AuthTextFieldProps<T extends FieldValues> = Omit<
  TextInputProps,
  'value' | 'onChangeText' | 'onBlur' | 'secureTextEntry'
> & {
  control: Control<T>;
  name: Path<T>;
  label: string;
  /** Renders a password field with a show/hide toggle. */
  isPassword?: boolean;
  error?: string;
};

export function AuthTextField<T extends FieldValues>({
  control,
  name,
  label,
  isPassword = false,
  error,
  ...inputProps
}: AuthTextFieldProps<T>) {
  const theme = useTheme();
  const [isHidden, setIsHidden] = useState(true);

  return (
    <View style={styles.container}>
      <ThemedText type="smallBold">{label}</ThemedText>
      <View
        style={[
          styles.inputRow,
          { backgroundColor: theme.backgroundElement },
          error ? styles.inputRowError : null,
        ]}>
        <Controller
          control={control}
          name={name}
          render={({ field }) => (
            <TextInput
              {...inputProps}
              accessibilityLabel={label}
              value={typeof field.value === 'string' ? field.value : ''}
              onChangeText={field.onChange}
              onBlur={field.onBlur}
              secureTextEntry={isPassword && isHidden}
              placeholderTextColor={theme.textSecondary}
              style={[styles.input, { color: theme.text }]}
            />
          )}
        />
        {isPassword && (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={isHidden ? 'Show password' : 'Hide password'}
            onPress={() => setIsHidden((hidden) => !hidden)}
            hitSlop={Spacing.two}>
            <ThemedText type="linkPrimary">{isHidden ? 'Show' : 'Hide'}</ThemedText>
          </Pressable>
        )}
      </View>
      {error ? (
        <ThemedText type="small" style={styles.errorText} accessibilityRole="alert">
          {error}
        </ThemedText>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: Spacing.one },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
    borderRadius: Spacing.three,
    borderWidth: 1,
    borderColor: 'transparent',
    paddingHorizontal: Spacing.three,
  },
  inputRowError: { borderColor: '#D93025' },
  input: { flex: 1, minHeight: 48, fontSize: 16 },
  errorText: { color: '#D93025' },
});
