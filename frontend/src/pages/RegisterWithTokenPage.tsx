/**
 * Public account activation (`/register/:token`).
 *
 * An admin pre-creates the user in OMS, which mints a one-time
 * UserRegistrationToken and a QR encoding `/register/<token>`. The new
 * member scans it, lands here, we validate the token + show who the
 * account is for, and they set their password to activate it. Single
 * use; the backend rejects an expired or already-used token.
 */
import {
  Anchor,
  Box,
  Button,
  Card,
  Center,
  Container,
  Group,
  Loader,
  PasswordInput,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { IconAlertTriangle, IconCheck, IconKey } from '@tabler/icons-react';
import dayjs from 'dayjs';
import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import '../styles/landing.css';
import { registrationPublicAPI, RegistrationTokenUser } from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const fullName = (user: RegistrationTokenUser): string =>
  [user.first_name, user.last_name].filter(Boolean).join(' ').trim() || user.username;

const RegisterWithTokenPage: React.FC = () => {
  const { token = '' } = useParams<{ token: string }>();
  const navigate = useNavigate();

  const [user, setUser] = useState<RegistrationTokenUser | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [validateError, setValidateError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [completed, setCompleted] = useState(false);

  const form = useForm({
    initialValues: { password: '', password2: '' },
    validate: {
      password: (v) => (v.length < 8 ? 'Password must be at least 8 characters' : null),
      password2: (v, values) => (v === values.password ? null : 'Passwords do not match'),
    },
  });

  useEffect(() => {
    if (!token) {
      setValidateError('Missing registration token in URL.');
      setLoading(false);
      return;
    }
    let cancelled = false;
    registrationPublicAPI
      .validate(token)
      .then((res) => {
        if (!cancelled) {
          setUser(res.data.user);
          setExpiresAt(res.data.expires_at);
        }
      })
      .catch((err) => {
        if (!cancelled)
          setValidateError(
            extractErrorMessage(err, 'This registration link is not valid or has expired.'),
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const handleSubmit = form.onSubmit(async (values) => {
    setSubmitting(true);
    try {
      await registrationPublicAPI.complete({
        token,
        password: values.password,
        password2: values.password2,
      });
      setCompleted(true);
    } catch (err) {
      const raw = (err as { response?: { data?: unknown } })?.response?.data ?? {};
      const details =
        (raw as { error?: { details?: Record<string, string[]> } })?.error?.details ??
        (raw as Record<string, string[] | string>);
      let mapped = false;
      for (const field of ['password', 'password2', 'token'] as const) {
        const value = (details as Record<string, unknown>)[field];
        if (value) {
          const target = field === 'token' ? 'password' : field;
          form.setFieldError(target, Array.isArray(value) ? value[0] : String(value));
          mapped = true;
        }
      }
      if (!mapped) {
        form.setFieldError(
          'password',
          extractErrorMessage(err, 'Could not activate the account. Try again.'),
        );
      }
    } finally {
      setSubmitting(false);
    }
  });

  if (loading) {
    return (
      <Box className="landing-surface">
        <Container size="sm" py="xl">
          <Center mih={240}>
            <Loader />
          </Center>
        </Container>
      </Box>
    );
  }

  if (validateError || !user) {
    return (
      <Box className="landing-surface">
        <Container size="sm" py="xl">
          <Stack gap="lg" align="center">
            <ThemeIcon size={72} radius="xl" color="red" variant="light">
              <IconAlertTriangle size={36} />
            </ThemeIcon>
            <Title order={2} ta="center">
              Registration link not valid
            </Title>
            <Text c="dimmed" ta="center" maw={420}>
              {validateError ??
                'This registration link has expired, already been used, or never existed. Ask an OMS admin for a fresh one.'}
            </Text>
            <Button variant="default" onClick={() => navigate('/')}>
              Back to homepage
            </Button>
          </Stack>
        </Container>
      </Box>
    );
  }

  if (completed) {
    return (
      <Box className="landing-surface">
        <Container size="sm" py="xl">
          <Stack gap="lg" align="center">
            <ThemeIcon size={72} radius="xl" color="green" variant="light">
              <IconCheck size={36} />
            </ThemeIcon>
            <Title order={2} ta="center">
              Account activated
            </Title>
            <Text c="dimmed" ta="center" maw={460}>
              You can sign in now as{' '}
              <Text component="span" fw={600}>
                {user.username}
              </Text>{' '}
              with the password you just set.
            </Text>
            <Button component={Link} to="/login">
              Sign in
            </Button>
          </Stack>
        </Container>
      </Box>
    );
  }

  return (
    <Box className="landing-surface">
      <Container size="sm" py="xl">
        <Card withBorder padding="lg" radius="md" data-testid="register-token-page">
          <Stack gap="md">
            <Group gap="xs">
              <IconKey size={20} />
              <Text className="landing-eyebrow">Activate your account</Text>
            </Group>
            <Title order={2}>{fullName(user)}</Title>
            <Text size="sm" c="dimmed">
              Setting up the OMS account for <strong>{user.username}</strong>
              {user.email ? ` (${user.email})` : ''}. Choose a password to finish.
              {expiresAt
                ? ` This link works once and expires ${dayjs(expiresAt).format('MMM D, YYYY')}.`
                : ' This link works once.'}
            </Text>

            <form onSubmit={handleSubmit}>
              <Stack gap="sm">
                <PasswordInput
                  label="Password"
                  required
                  description="8+ characters. Pick something a password manager generates."
                  autoComplete="new-password"
                  {...form.getInputProps('password')}
                />
                <PasswordInput
                  label="Confirm password"
                  required
                  autoComplete="new-password"
                  {...form.getInputProps('password2')}
                />
                <Group justify="space-between" mt="sm">
                  <Anchor component={Link} to="/" size="sm" c="dimmed">
                    Cancel
                  </Anchor>
                  <Button type="submit" loading={submitting}>
                    Activate account
                  </Button>
                </Group>
              </Stack>
            </form>
          </Stack>
        </Card>
      </Container>
    </Box>
  );
};

export default RegisterWithTokenPage;
