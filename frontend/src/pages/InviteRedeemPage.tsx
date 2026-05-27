/**
 * Public invite redemption (`/invite/:code`).
 *
 * Anonymous landing — the invitee follows a link the staff member
 * generated on /admin/invite-codes. We hit the preview endpoint to
 * confirm the code is open + show what role they're signing up for,
 * then a redeem form creates their OMS account. Single-use; the
 * backend transactionally rejects a second redeem.
 */
import {
  Anchor,
  Badge,
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
  TextInput,
  ThemeIcon,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { IconAlertTriangle, IconCheck, IconTicket } from '@tabler/icons-react';
import dayjs from 'dayjs';
import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import '../styles/landing.css';
import {
  invitePublicAPI,
  InvitePreviewResponse,
  InviteRedeemPayload,
} from '../services/api';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const InviteRedeemPage: React.FC = () => {
  const { code = '' } = useParams<{ code: string }>();
  const navigate = useNavigate();

  const [preview, setPreview] = useState<InvitePreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [completedUsername, setCompletedUsername] = useState<string | null>(null);

  const form = useForm({
    initialValues: {
      username: '',
      email: '',
      password: '',
      password_confirm: '',
      first_name: '',
      last_name: '',
    },
    validate: {
      username: (v) => (v.trim().length < 3 ? 'Username must be at least 3 characters' : null),
      email: (v) => (/^\S+@\S+\.\S+$/.test(v) ? null : 'Enter a valid email'),
      password: (v) =>
        v.length < 12 ? 'Password must be at least 12 characters' : null,
      password_confirm: (v, values) =>
        v === values.password ? null : 'Passwords do not match',
    },
  });

  useEffect(() => {
    if (!code) {
      setPreviewError('Missing invite code in URL.');
      setLoading(false);
      return;
    }
    let cancelled = false;
    invitePublicAPI
      .preview(code)
      .then((res) => {
        if (!cancelled) setPreview(res.data);
      })
      .catch((err) => {
        if (!cancelled)
          setPreviewError(
            extractErrorMessage(err, 'This invite link is not valid or has expired.'),
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  const handleSubmit = form.onSubmit(async (values) => {
    setSubmitting(true);
    try {
      const payload: InviteRedeemPayload = {
        code,
        username: values.username.trim(),
        email: values.email.trim(),
        password: values.password,
        first_name: values.first_name.trim(),
        last_name: values.last_name.trim(),
      };
      const response = await invitePublicAPI.redeem(payload);
      setCompletedUsername(response.data.username);
    } catch (err) {
      // Map field-level backend errors back onto the form.
      // Standardized envelope: {error: {details: {field: [msg]}}}
      // or DRF default {field: [msg]} for the redeem path.
      const raw = (err as { response?: { data?: unknown } })?.response?.data ?? {};
      const details =
        (raw as { error?: { details?: Record<string, string[]> } })?.error?.details ??
        (raw as Record<string, string[] | string>);
      let mapped = false;
      for (const field of [
        'username',
        'email',
        'password',
        'first_name',
        'last_name',
        'code',
      ] as const) {
        const value = (details as Record<string, unknown>)[field];
        if (value) {
          form.setFieldError(field, Array.isArray(value) ? value[0] : String(value));
          mapped = true;
        }
      }
      if (!mapped) {
        form.setFieldError(
          'password',
          extractErrorMessage(err, 'Could not complete signup. Try again.'),
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

  if (previewError || !preview) {
    return (
      <Box className="landing-surface">
        <Container size="sm" py="xl">
          <Stack gap="lg" align="center">
            <ThemeIcon size={72} radius="xl" color="red" variant="light">
              <IconAlertTriangle size={36} />
            </ThemeIcon>
            <Title order={2} ta="center">
              Invite link not valid
            </Title>
            <Text c="dimmed" ta="center" maw={420}>
              {previewError ??
                'This invite has expired, already been redeemed, or never existed. Reach out to whoever sent it to you for a fresh link.'}
            </Text>
            <Button variant="default" onClick={() => navigate('/')}>
              Back to homepage
            </Button>
          </Stack>
        </Container>
      </Box>
    );
  }

  if (completedUsername) {
    return (
      <Box className="landing-surface">
        <Container size="sm" py="xl">
          <Stack gap="lg" align="center">
            <ThemeIcon size={72} radius="xl" color="green" variant="light">
              <IconCheck size={36} />
            </ThemeIcon>
            <Title order={2} ta="center">
              Account created
            </Title>
            <Text c="dimmed" ta="center" maw={460}>
              Welcome,{' '}
              <Text component="span" fw={600}>
                {completedUsername}
              </Text>
              . You can sign in now with the username and password you just set.
              ForgeKey door access for your role activates within a minute or two.
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
        <Card withBorder padding="lg" radius="md" data-testid="invite-redeem-page">
          <Stack gap="md">
            <Group gap="xs">
              <IconTicket size={20} />
              <Text className="landing-eyebrow">Onboarding invite</Text>
            </Group>
            <Title order={2}>{preview.intended_label}</Title>
            {preview.intended_group_names.length > 0 && (
              <Group gap={4}>
                <Text size="sm" c="dimmed">
                  Joining:
                </Text>
                {preview.intended_group_names.map((name) => (
                  <Badge key={name} variant="light">
                    {name}
                  </Badge>
                ))}
              </Group>
            )}
            <Text size="sm" c="dimmed">
              This invite link expires {dayjs(preview.expires_at).format('MMM D, YYYY')} and
              works once. Fill in the details below to create your OMS account.
            </Text>

            <form onSubmit={handleSubmit}>
              <Stack gap="sm">
                <Group grow>
                  <TextInput
                    label="First name"
                    {...form.getInputProps('first_name')}
                  />
                  <TextInput label="Last name" {...form.getInputProps('last_name')} />
                </Group>
                <TextInput
                  label="Username"
                  required
                  description="3+ characters. This is what you sign in with."
                  autoComplete="username"
                  {...form.getInputProps('username')}
                />
                <TextInput
                  label="Email"
                  required
                  type="email"
                  autoComplete="email"
                  {...form.getInputProps('email')}
                />
                <PasswordInput
                  label="Password"
                  required
                  description="12+ characters. Pick something a password manager generates."
                  autoComplete="new-password"
                  {...form.getInputProps('password')}
                />
                <PasswordInput
                  label="Confirm password"
                  required
                  autoComplete="new-password"
                  {...form.getInputProps('password_confirm')}
                />
                <Group justify="space-between" mt="sm">
                  <Anchor component={Link} to="/" size="sm" c="dimmed">
                    Cancel
                  </Anchor>
                  <Button type="submit" loading={submitting}>
                    Create account
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

export default InviteRedeemPage;
