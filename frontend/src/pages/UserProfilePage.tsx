
/**
 * User Profile Page
 * Allows users to view and edit their profile, change password, upload signature, and manage notification preferences
 */
import { zodResolver } from '@hookform/resolvers/zod';
import { Alert, Button, Group, Image, Paper, PasswordInput, Slider, Stack, Switch, Tabs, Text } from '@mantine/core';
import { IconAlertCircle, IconCheck } from '@tabler/icons-react';
import { useCallback, useEffect, useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { z } from 'zod';
import { FormImageUpload } from '../components/forms/FormImageUpload';
import { FormInput } from '../components/forms/FormInput';
import WorkspacePage from '../components/landing/WorkspacePage';
import { notificationsAPI, userAPI } from '../services/api';
import { ChangePasswordRequest, NotificationPreferences, UserProfile } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

// Form schemas
const profileSchema = z.object({
  email: z.string().email('Invalid email address'),
  first_name: z.string().min(1, 'First name is required'),
  last_name: z.string().min(1, 'Last name is required'),
  handle: z.string().optional().nullable(),
  discord_username: z.string().optional().nullable(),
  discourse_username: z.string().optional().nullable(),
});

const passwordSchema = z.object({
  old_password: z.string().min(1, 'Current password is required'),
  new_password: z.string().min(8, 'Password must be at least 8 characters'),
  new_password2: z.string().min(1, 'Please confirm your password'),
}).refine((data) => data.new_password === data.new_password2, {
  message: "Passwords don't match",
  path: ["new_password2"],
});

const signatureSchema = z.object({
  signature: z.instanceof(File).optional().nullable(),
  password: z.string().min(1, 'Password is required to upload signature'),
});

type ProfileFormData = z.infer<typeof profileSchema>;
type PasswordFormData = z.infer<typeof passwordSchema>;
type SignatureFormData = z.infer<typeof signatureSchema>;

const UserProfilePage: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string | null>('profile');
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [preferences, setPreferences] = useState<NotificationPreferences | null>(null);
  const [preferencesLoading, setPreferencesLoading] = useState(true);
  const [preferencesError, setPreferencesError] = useState<string | null>(null);
  const [signaturePreview, setSignaturePreview] = useState<string | null>(null);

  // Profile form
  const profileForm = useForm<ProfileFormData>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
      email: '',
      first_name: '',
      last_name: '',
      handle: null,
      discord_username: null,
      discourse_username: null,
    },
  });

  // Password form
  const passwordForm = useForm<PasswordFormData>({
    resolver: zodResolver(passwordSchema),
    defaultValues: {
      old_password: '',
      new_password: '',
      new_password2: '',
    },
  });

  // Signature form
  const signatureForm = useForm<SignatureFormData>({
    resolver: zodResolver(signatureSchema),
    defaultValues: {
      signature: null,
      password: '',
    },
  });

  const loadProfile = useCallback(async () => {
    try {
      setLoading(true);
      const response = await userAPI.getProfile();
      const profileData = response.data;
      setProfile(profileData);
      profileForm.reset({
        email: profileData.email,
        first_name: profileData.first_name,
        last_name: profileData.last_name,
        handle: profileData.handle || null,
        discord_username: profileData.discord_username || null,
        discourse_username: profileData.discourse_username || null,
      });
      if (profileData.signature_image_url) {
        setSignaturePreview(profileData.signature_image_url);
      }
    } catch (err: any) {
      console.error('Error loading profile:', err);
      setError(extractErrorMessage(err, 'Failed to load profile'));
    } finally {
      setLoading(false);
    }
  }, [profileForm]);

  const loadPreferences = useCallback(async () => {
    try {
      setPreferencesLoading(true);
      setPreferencesError(null);
      const response = await notificationsAPI.getPreferences();
      setPreferences(response.data);
    } catch (err: any) {
      console.error('Error loading preferences:', err);
      const errorMessage = extractErrorMessage(err, '') || err.message || 'Failed to load notification preferences';
      setPreferencesError(errorMessage);
      setError(`Failed to load notification preferences: ${errorMessage}`);
    } finally {
      setPreferencesLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProfile();
    loadPreferences();
  }, [loadProfile, loadPreferences]);

  const onProfileSubmit = async (data: ProfileFormData) => {
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);
      const response = await userAPI.updateProfile(data);
      setProfile(response.data);
      setSuccess('Profile updated successfully');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err: any) {
      console.error('Error updating profile:', err);
      setError(extractErrorMessage(err, 'Failed to update profile'));
    } finally {
      setSaving(false);
    }
  };

  const onPasswordSubmit = async (data: PasswordFormData) => {
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);
      await userAPI.changePassword(data as ChangePasswordRequest);
      setSuccess('Password changed successfully');
      passwordForm.reset();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err: any) {
      console.error('Error changing password:', err);
      const errorData = err.response?.data;
      if (errorData?.old_password) {
        setError(errorData.old_password[0] || 'Invalid current password');
      } else {
        setError(errorData?.detail || 'Failed to change password');
      }
    } finally {
      setSaving(false);
    }
  };

  const onSignatureSubmit = async (data: SignatureFormData) => {
    if (!data.signature) {
      setError('Please select a signature file');
      return;
    }

    try {
      setSaving(true);
      setError(null);
      setSuccess(null);
      const response = await userAPI.uploadSignature(data.signature, data.password);
      setSuccess('Signature uploaded successfully');
      if (response.data.signature_url) {
        setSignaturePreview(response.data.signature_url);
      }
      signatureForm.reset({ signature: null, password: '' });
      // Reload profile to get updated signature URL
      await loadProfile();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err: any) {
      console.error('Error uploading signature:', err);
      const errorData = err.response?.data;
      if (errorData?.error) {
        setError(errorData.error);
      } else {
        setError('Failed to upload signature');
      }
    } finally {
      setSaving(false);
    }
  };

  const onPreferencesChange = async (field: keyof NotificationPreferences, value: boolean | number) => {
    if (!preferences) return;

    try {
      const response = await notificationsAPI.updatePreferences({ [field]: value });
      setPreferences(response.data);
      // Dispatch custom event to notify other components (e.g., RecentPages)
      window.dispatchEvent(new CustomEvent('preferencesUpdated'));
    } catch (err: any) {
      console.error('Error updating preferences:', err);
      setError('Failed to update notification preferences');
    }
  };

  if (loading) {
    return (
      <WorkspacePage
        testId="user-profile-page"
        hero={{ eyebrow: 'Settings', title: 'Profile', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading profile…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="user-profile-page"
      hero={{
        eyebrow: 'Settings',
        title: 'Profile',
        description: 'Personal info, password, signature, and notification preferences.',
      }}
    >
      {error && (
        <Alert icon={<IconAlertCircle size={16} />} title="Error" color="red" onClose={() => setError(null)} withCloseButton>
          {error}
        </Alert>
      )}

      {success && (
        <Alert icon={<IconCheck size={16} />} title="Success" color="green" onClose={() => setSuccess(null)} withCloseButton>
          {success}
        </Alert>
      )}

      <Tabs value={activeTab} onChange={setActiveTab}>
        <Tabs.List>
          <Tabs.Tab value="profile">Profile</Tabs.Tab>
          <Tabs.Tab value="password">Change Password</Tabs.Tab>
          <Tabs.Tab value="signature">Signature</Tabs.Tab>
          <Tabs.Tab value="notifications">Notifications</Tabs.Tab>
        </Tabs.List>

        {/* Profile Tab */}
        <Tabs.Panel value="profile" pt="md">
          <Paper p="md" withBorder>
            <form onSubmit={profileForm.handleSubmit(onProfileSubmit)}>
              <Stack gap="md">
                <FormInput
                  name="email"
                  control={profileForm.control}
                  label="Email"
                  type="email"
                  required
                />
                <FormInput
                  name="first_name"
                  control={profileForm.control}
                  label="First Name"
                  required
                />
                <FormInput
                  name="last_name"
                  control={profileForm.control}
                  label="Last Name"
                  required
                />
                <FormInput
                  name="handle"
                  control={profileForm.control}
                  label="Handle"
                  description="Your preferred display name"
                />
                <FormInput
                  name="discord_username"
                  control={profileForm.control}
                  label="Discord Username"
                />
                <FormInput
                  name="discourse_username"
                  control={profileForm.control}
                  label="Discourse Username"
                />
                <Group justify="flex-end">
                  <Button type="submit" loading={saving}>
                    Save Changes
                  </Button>
                </Group>
              </Stack>
            </form>
          </Paper>
        </Tabs.Panel>

        {/* Password Tab */}
        <Tabs.Panel value="password" pt="md">
          <Paper p="md" withBorder>
            <form onSubmit={passwordForm.handleSubmit(onPasswordSubmit)}>
              <Stack gap="md">
                <Controller
                  name="old_password"
                  control={passwordForm.control}
                  render={({ field, fieldState: { error } }) => (
                    <PasswordInput
                      {...field}
                      label="Current Password"
                      required
                      error={error?.message}
                    />
                  )}
                />
                <Controller
                  name="new_password"
                  control={passwordForm.control}
                  render={({ field, fieldState: { error } }) => (
                    <PasswordInput
                      {...field}
                      label="New Password"
                      required
                      error={error?.message}
                    />
                  )}
                />
                <Controller
                  name="new_password2"
                  control={passwordForm.control}
                  render={({ field, fieldState: { error } }) => (
                    <PasswordInput
                      {...field}
                      label="Confirm New Password"
                      required
                      error={error?.message}
                    />
                  )}
                />
                <Group justify="flex-end">
                  <Button type="submit" loading={saving}>
                    Change Password
                  </Button>
                </Group>
              </Stack>
            </form>
          </Paper>
        </Tabs.Panel>

        {/* Signature Tab */}
        <Tabs.Panel value="signature" pt="md">
          <Paper p="md" withBorder>
            <Stack gap="md">
              {signaturePreview && (
                <Stack gap="xs">
                  <Text size="sm" fw={500}>Current Signature</Text>
                  <Image src={signaturePreview} alt="Current signature" height={150} fit="contain" style={{ border: '1px solid #ddd', borderRadius: '4px' }} />
                </Stack>
              )}
              <form onSubmit={signatureForm.handleSubmit(onSignatureSubmit)}>
                <Stack gap="md">
                  <FormImageUpload
                    name="signature"
                    control={signatureForm.control}
                    label="Upload Signature"
                    description="Upload a PNG image of your signature (PNG files only)"
                    accept={['image/png']}
                    maxSize={5 * 1024 * 1024}
                  />
                  <Controller
                    name="password"
                    control={signatureForm.control}
                    render={({ field, fieldState: { error } }) => (
                      <PasswordInput
                        {...field}
                        label="Password"
                        description="Enter your password to verify signature upload"
                        required
                        error={error?.message}
                      />
                    )}
                  />
                  <Group justify="flex-end">
                    <Button type="submit" loading={saving}>
                      Upload Signature
                    </Button>
                  </Group>
                </Stack>
              </form>
            </Stack>
          </Paper>
        </Tabs.Panel>

        {/* Notifications Tab */}
        <Tabs.Panel value="notifications" pt="md">
          <Paper p="md" withBorder>
            <Stack gap="md">
              <Text size="sm" c="dimmed">
                Manage your notification preferences. Changes are saved automatically.
              </Text>
              {preferencesError && (
                <Alert icon={<IconAlertCircle size={16} />} title="Error Loading Preferences" color="red" onClose={() => setPreferencesError(null)} withCloseButton>
                  {preferencesError}
                  <Button size="xs" mt="xs" onClick={loadPreferences}>
                    Retry
                  </Button>
                </Alert>
              )}
              {preferencesLoading ? (
                <Text size="sm" c="dimmed">Loading preferences...</Text>
              ) : preferences ? (
                <Stack gap="md">
                  <Switch
                    label="Email Notifications"
                    description="Receive notifications via email"
                    checked={preferences.email_enabled}
                    onChange={(e) => onPreferencesChange('email_enabled', e.currentTarget.checked)}
                  />
                  <Switch
                    label="In-App Notifications"
                    description="Receive notifications in the application"
                    checked={preferences.in_app_enabled}
                    onChange={(e) => onPreferencesChange('in_app_enabled', e.currentTarget.checked)}
                  />
                  <Switch
                    label="Supply Alerts"
                    description="Receive alerts about low supplies"
                    checked={preferences.supply_alerts}
                    onChange={(e) => onPreferencesChange('supply_alerts', e.currentTarget.checked)}
                  />
                  <Switch
                    label="Maintenance Alerts"
                    description="Receive alerts about maintenance needs"
                    checked={preferences.maintenance_alerts}
                    onChange={(e) => onPreferencesChange('maintenance_alerts', e.currentTarget.checked)}
                  />
                  <Switch
                    label="Order Updates"
                    description="Receive updates about order status"
                    checked={preferences.order_updates}
                    onChange={(e) => onPreferencesChange('order_updates', e.currentTarget.checked)}
                  />
                  <Switch
                    label="System Notifications"
                    description="Receive system notifications"
                    checked={preferences.system_notifications}
                    onChange={(e) => onPreferencesChange('system_notifications', e.currentTarget.checked)}
                  />
                  <div>
                    <Text size="sm" fw={500} mb="xs">
                      Recent Pages Limit
                    </Text>
                    <Text size="xs" c="dimmed" mb="md">
                      Maximum number of recent pages to display in the sidebar (1-50)
                    </Text>
                    <Slider
                      value={preferences.recent_pages_limit}
                      onChange={(value) => onPreferencesChange('recent_pages_limit', value)}
                      min={1}
                      max={50}
                      step={1}
                      marks={[
                        { value: 1, label: '1' },
                        { value: 10, label: '10' },
                        { value: 25, label: '25' },
                        { value: 50, label: '50' },
                      ]}
                      mb="md"
                    />
                    <Text size="xs" c="dimmed" ta="center">
                      Current: {preferences.recent_pages_limit} pages
                    </Text>
                  </div>
                </Stack>
              ) : (
                <Text size="sm" c="dimmed">
                  {preferencesError ? 'Failed to load preferences. Please try again.' : 'No preferences available.'}
                </Text>
              )}
            </Stack>
          </Paper>
        </Tabs.Panel>
      </Tabs>
    </WorkspacePage>
  );
};

export default UserProfilePage;
