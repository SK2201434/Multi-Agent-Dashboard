import { zodResolver } from '@hookform/resolvers/zod';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Pressable, StyleSheet } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { AuthButton } from '@/features/auth/AuthButton';
import { getAuthErrorMessage } from '@/features/auth/authErrorMessage';
import { AuthFormLayout } from '@/features/auth/AuthFormLayout';
import { AuthTextField } from '@/features/auth/AuthTextField';
import { ownerLoginSchema, type OwnerLoginFormValues } from '@/features/auth/authValidation';
import { fetchCurrentOwnerThunk, loginOwnerThunk } from '@/features/auth/ownerAuthSlice';
import { useAppDispatch } from '@/store/hooks';

type OwnerLoginScreenProps = { onShowRegister: () => void };

export function OwnerLoginScreen({ onShowRegister }: OwnerLoginScreenProps) {
  const dispatch = useAppDispatch();
  const [submitError, setSubmitError] = useState<string | null>(null);

  const {
    control,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<OwnerLoginFormValues>({
    resolver: zodResolver(ownerLoginSchema),
    defaultValues: { email: '', password: '' },
  });

  // Login saves the token but does not authenticate by itself. The owner is signed in
  // only once `/me` succeeds, at which point OwnerAuthGate swaps this screen out.
  const onSubmit = handleSubmit(async (values) => {
    setSubmitError(null);
    try {
      await dispatch(loginOwnerThunk(values)).unwrap();
      await dispatch(fetchCurrentOwnerThunk()).unwrap();
    } catch (error) {
      setSubmitError(getAuthErrorMessage(error));
    }
  });

  return (
    <AuthFormLayout title="Owner login">
      <AuthTextField
        control={control}
        name="email"
        label="Email"
        error={errors.email?.message}
        keyboardType="email-address"
        autoCapitalize="none"
        autoComplete="email"
        autoCorrect={false}
        textContentType="emailAddress"
        returnKeyType="next"
        editable={!isSubmitting}
      />
      <AuthTextField
        control={control}
        name="password"
        label="Password"
        isPassword
        error={errors.password?.message}
        autoCapitalize="none"
        autoComplete="current-password"
        textContentType="password"
        returnKeyType="done"
        onSubmitEditing={onSubmit}
        editable={!isSubmitting}
      />

      {submitError ? (
        <ThemedText type="small" style={styles.error} accessibilityRole="alert">
          {submitError}
        </ThemedText>
      ) : null}

      <AuthButton label="Log in" onPress={onSubmit} isLoading={isSubmitting} />

      <Pressable
        accessibilityRole="button"
        onPress={onShowRegister}
        disabled={isSubmitting}
        style={styles.link}>
        <ThemedText type="linkPrimary">New owner? Create an account</ThemedText>
      </Pressable>
    </AuthFormLayout>
  );
}

const styles = StyleSheet.create({
  error: { color: '#D93025' },
  link: { alignItems: 'center', paddingVertical: 8 },
});
