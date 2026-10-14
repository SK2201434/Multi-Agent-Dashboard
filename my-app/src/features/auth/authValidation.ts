import { z } from 'zod';

// Mirrors the backend constraints in backend/app/schemas/auth.py.
// Input is intentionally not trimmed or lowercased; the backend normalizes it.

const email = z
  .string({ error: 'Email is required.' })
  .min(1, 'Email is required.')
  .pipe(z.email('Enter a valid email address.'));

export const ownerRegisterSchema = z.object({
  full_name: z
    .string({ error: 'Full name is required.' })
    .min(2, 'Full name must be at least 2 characters.')
    .max(100, 'Full name must be at most 100 characters.'),
  email,
  password: z
    .string({ error: 'Password is required.' })
    .min(8, 'Password must be at least 8 characters.')
    .max(128, 'Password must be at most 128 characters.'),
});

export const ownerLoginSchema = z.object({
  email,
  password: z.string({ error: 'Password is required.' }).min(1, 'Password is required.'),
});

export type OwnerRegisterFormValues = z.infer<typeof ownerRegisterSchema>;
export type OwnerLoginFormValues = z.infer<typeof ownerLoginSchema>;

// Form-only schema: `confirm_password` is validated on the client and never sent to the API.
export const ownerRegisterFormSchema = ownerRegisterSchema
  .extend({
    confirm_password: z
      .string({ error: 'Please confirm your password.' })
      .min(1, 'Please confirm your password.'),
  })
  .refine((values) => values.password === values.confirm_password, {
    path: ['confirm_password'],
    message: 'Passwords do not match.',
  });

export type OwnerRegisterFormInput = z.infer<typeof ownerRegisterFormSchema>;
