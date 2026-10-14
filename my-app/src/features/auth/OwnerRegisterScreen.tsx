import { zodResolver } from '@hookform/resolvers/zod';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Pressable, StyleSheet } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { AuthButton } from '@/features/auth/AuthButton';
import { getAuthErrorMessage } from '@/features/auth/authErrorMessage';
import { AuthFormLayout } from '@/features/auth/AuthFormLayout';
import { AuthTextField } from '@/features/auth/AuthTextField';
import {
  ownerRegisterFormSchema,
  type OwnerRegisterFormInput,
} from '@/features/auth/authValidation';
import { registerOwnerThunk } from '@/features/auth/ownerAuthSlice';
import { useAppDispatch } from '@/store/hooks';

type OwnerRegisterScreenProps = { onShowLogin: () => void };

export function OwnerRegisterScreen({ onShowLogin }: OwnerRegisterScreenProps) {
  const dispatch = useAppDispatch();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isRegistered, setIsRegistered] = useState(false);

  const {
    control,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<OwnerRegisterFormInput>({
    resolver: zodResolver(ownerRegisterFormSchema),
    defaultValues: { full_name: '', email: '', password: '', confirm_password: '' },
  });

  // Registration does not return a token, so it never signs the owner in.
  const onSubmit = handleSubmit(async ({ full_name, email, password }) => {
    setSubmitError(null);
    try {
      // Only the backend fields are sent; `confirm_password` stays on the client.
      await dispatch(registerOwnerThunk({ full_name, email, password })).unwrap();
      setIsRegistered(true);
    } catch (error) {
      setSubmitError(getAuthErrorMessage(error));
    }
  });

  if (isRegistered) {
    return (
      <AuthFormLayout title="Account created">
        <ThemedText accessibilityRole="alert">
          Your owner account was created successfully. Log in to continue.
        </ThemedText>
        <AuthButton label="Go to login" onPress={onShowLogin} />
      </AuthFormLayout>
    );
  }

  return (
    <AuthFormLayout title="Create owner account">
      <AuthTextField
        control={control}
        name="full_name"
        label="Full name"
        error={errors.full_name?.message}
        autoCapitalize="words"
        autoComplete="name"
        textContentType="name"
        returnKeyType="next"
        editable={!isSubmitting}
      />
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
        autoComplete="new-password"
        textContentType="newPassword"
        returnKeyType="next"
        editable={!isSubmitting}
      />
      <AuthTextField
        control={control}
        name="confirm_password"
        label="Confirm password"
        isPassword
        error={errors.confirm_password?.message}
        autoCapitalize="none"
        autoComplete="new-password"
        textContentType="newPassword"
        returnKeyType="done"
        onSubmitEditing={onSubmit}
        editable={!isSubmitting}
      />

      {submitError ? (
        <ThemedText type="small" style={styles.error} accessibilityRole="alert">
          {submitError}
        </ThemedText>
      ) : null}

      <AuthButton label="Register" onPress={onSubmit} isLoading={isSubmitting} />

      <Pressable
        accessibilityRole="button"
        onPress={onShowLogin}
        disabled={isSubmitting}
        style={styles.link}>
        <ThemedText type="linkPrimary">Already have an account? Log in</ThemedText>
      </Pressable>
    </AuthFormLayout>
  );
}

const styles = StyleSheet.create({
  error: { color: '#D93025' },
  link: { alignItems: 'center', paddingVertical: 8 },
});
