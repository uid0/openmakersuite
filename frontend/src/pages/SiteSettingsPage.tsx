/**
 * Site Settings Page
 * Allows superusers to manage site-wide customization settings
 */
import { zodResolver } from '@hookform/resolvers/zod';
import { Alert, Button, Group, Image, Paper, Stack, Text, Title } from '@mantine/core';
import { IconAlertCircle, IconCheck } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useNavigate } from 'react-router-dom';
import { z } from 'zod';
import ColorPicker from '../components/ColorPicker';
import { FormImageUpload } from '../components/forms/FormImageUpload';
import { FormInput } from '../components/forms/FormInput';
import { customizationAPI } from '../services/api';
import '../styles/SiteSettingsPage.css';
import { SiteSettings } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';

// Form schema
const siteSettingsSchema = z.object({
  site_name: z.string().min(1, 'Site name is required').max(200, 'Site name must be 200 characters or less'),
  site_tagline: z.string().max(300, 'Tagline must be 300 characters or less').optional().nullable(),
  logo: z.instanceof(File).optional().nullable(),
  logo_alt_text: z.string().max(200, 'Alt text must be 200 characters or less').optional().nullable(),
  primary_color: z.string().regex(/^#[0-9A-Fa-f]{6}$/, 'Primary color must be a valid hex color (#RRGGBB)'),
  secondary_color: z.string().regex(/^#[0-9A-Fa-f]{6}$/, 'Secondary color must be a valid hex color (#RRGGBB)'),
  contact_email: z.string().email('Invalid email address').optional().nullable().or(z.literal('')),
  contact_phone: z.string().max(20, 'Phone number must be 20 characters or less').optional().nullable(),
  website_url: z.string().url('Invalid URL format').optional().nullable().or(z.literal('')),
});

type SiteSettingsFormData = z.infer<typeof siteSettingsSchema>;

const SiteSettingsPage: React.FC = () => {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [settings, setSettings] = useState<SiteSettings | null>(null);
  const [currentLogoUrl, setCurrentLogoUrl] = useState<string | null>(null);
  const [removeLogo, setRemoveLogo] = useState(false);

  // Check if user is superuser
  useEffect(() => {
    const isSuperuser = localStorage.getItem('is_superuser') === 'true';
    if (!isSuperuser) {
      navigate('/');
      return;
    }
    loadSettings();
  }, [navigate]);

  const form = useForm<SiteSettingsFormData>({
    resolver: zodResolver(siteSettingsSchema),
    defaultValues: {
      site_name: '',
      site_tagline: null,
      logo: null,
      logo_alt_text: null,
      primary_color: '#007cba',
      secondary_color: '#417690',
      contact_email: null,
      contact_phone: null,
      website_url: null,
    },
  });

  const loadSettings = async () => {
    try {
      setLoading(true);
      const response = await customizationAPI.getSiteSettings();
      const settingsData = response.data;
      setSettings(settingsData);
      setCurrentLogoUrl(settingsData.logo_url);
      form.reset({
        site_name: settingsData.site_name,
        site_tagline: settingsData.site_tagline || null,
        logo: null,
        logo_alt_text: settingsData.logo_alt_text || null,
        primary_color: settingsData.primary_color,
        secondary_color: settingsData.secondary_color,
        contact_email: settingsData.contact_email || null,
        contact_phone: settingsData.contact_phone || null,
        website_url: settingsData.website_url || null,
      });
    } catch (err: any) {
      console.error('Error loading site settings:', err);
      setError(extractErrorMessage(err, 'Failed to load site settings'));
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = async (data: SiteSettingsFormData) => {
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);

      // Create FormData for multipart/form-data upload
      const formData = new FormData();
      formData.append('site_name', data.site_name);
      if (data.site_tagline) {
        formData.append('site_tagline', data.site_tagline);
      }
      if (data.logo) {
        formData.append('logo', data.logo);
      } else if (removeLogo) {
        // Send empty string to remove logo
        formData.append('logo', '');
      }
      if (data.logo_alt_text) {
        formData.append('logo_alt_text', data.logo_alt_text);
      }
      formData.append('primary_color', data.primary_color);
      formData.append('secondary_color', data.secondary_color);
      if (data.contact_email) {
        formData.append('contact_email', data.contact_email);
      }
      if (data.contact_phone) {
        formData.append('contact_phone', data.contact_phone);
      }
      if (data.website_url) {
        formData.append('website_url', data.website_url);
      }

      const response = await customizationAPI.updateSiteSettings(formData);
      setSettings(response.data);
      if (response.data.logo_url) {
        setCurrentLogoUrl(response.data.logo_url);
      } else {
        setCurrentLogoUrl(null);
      }
      setRemoveLogo(false); // Reset remove flag
      setSuccess('Site settings updated successfully');
      setTimeout(() => setSuccess(null), 3000);
      
      // Reset form with new data (but keep logo file if not changed)
      form.reset({
        ...data,
        logo: null, // Clear the file input after successful upload
      });
    } catch (err: any) {
      console.error('Error updating site settings:', err);
      setError(extractErrorMessage(err, 'Failed to update site settings'));
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveLogo = () => {
    form.setValue('logo', null);
    setCurrentLogoUrl(null);
    setRemoveLogo(true);
  };

  if (loading) {
    return (
      <Stack gap="md">
        <Title order={2}>Site Settings</Title>
        <Text>Loading...</Text>
      </Stack>
    );
  }

  return (
    <div className="site-settings-page">
      <Stack gap="md">
        <Title order={2}>Site Settings</Title>
        <Text c="dimmed" size="sm">
          Manage site-wide customization settings. Only superusers can access this page.
        </Text>

        {error && (
          <Alert
            icon={<IconAlertCircle size={16} />}
            title="Error"
            color="red"
            onClose={() => setError(null)}
            withCloseButton
          >
            {error}
          </Alert>
        )}

        {success && (
          <Alert
            icon={<IconCheck size={16} />}
            title="Success"
            color="green"
            onClose={() => setSuccess(null)}
            withCloseButton
          >
            {success}
          </Alert>
        )}

        <form onSubmit={form.handleSubmit(onSubmit)}>
          <Stack gap="lg">
            {/* Logo Upload Section */}
            <Paper p="md" withBorder>
              <Stack gap="md">
                <Title order={4}>Logo</Title>
                <Text size="sm" c="dimmed">
                  Upload your organization's logo. Recommended size: 200x50px or similar aspect ratio.
                </Text>
                
                {currentLogoUrl && (
                  <div className="logo-preview-section">
                    <Text size="sm" fw={500} mb="xs">
                      Current Logo:
                    </Text>
                    <Image src={currentLogoUrl} alt={settings?.logo_alt_text || 'Site Logo'} height={100} fit="contain" />
                    <Button
                      variant="outline"
                      color="red"
                      size="xs"
                      mt="xs"
                      onClick={handleRemoveLogo}
                    >
                      Remove Logo
                    </Button>
                  </div>
                )}

                <FormImageUpload
                  name="logo"
                  control={form.control}
                  label="Upload New Logo"
                  description="Select a new logo image to replace the current one"
                  maxSize={5 * 1024 * 1024} // 5MB
                />

                <FormInput
                  name="logo_alt_text"
                  control={form.control}
                  label="Logo Alt Text"
                  description="Alt text for the logo image (for accessibility)"
                  maxLength={200}
                />
              </Stack>
            </Paper>

            {/* Site Information Section */}
            <Paper p="md" withBorder>
              <Stack gap="md">
                <Title order={4}>Site Information</Title>
                <FormInput
                  name="site_name"
                  control={form.control}
                  label="Site Name"
                  description="The name of your makerspace or organization"
                  required
                  maxLength={200}
                />
                <FormInput
                  name="site_tagline"
                  control={form.control}
                  label="Site Tagline"
                  description="Optional tagline or subtitle for your site"
                  maxLength={300}
                />
              </Stack>
            </Paper>

            {/* Color Customization Section */}
            <Paper p="md" withBorder>
              <Stack gap="md">
                <Title order={4}>Color Customization</Title>
                <Text size="sm" c="dimmed">
                  Customize the primary and secondary brand colors for your site.
                </Text>
                <Controller
                  name="primary_color"
                  control={form.control}
                  render={({ field, fieldState: { error } }) => (
                    <div>
                      <ColorPicker
                        value={field.value}
                        onChange={field.onChange}
                        label="Primary Color"
                      />
                      {error && (
                        <Text size="sm" c="red" mt="xs">
                          {error.message}
                        </Text>
                      )}
                    </div>
                  )}
                />
                <Controller
                  name="secondary_color"
                  control={form.control}
                  render={({ field, fieldState: { error } }) => (
                    <div>
                      <ColorPicker
                        value={field.value}
                        onChange={field.onChange}
                        label="Secondary Color"
                      />
                      {error && (
                        <Text size="sm" c="red" mt="xs">
                          {error.message}
                        </Text>
                      )}
                    </div>
                  )}
                />
              </Stack>
            </Paper>

            {/* Contact Information Section */}
            <Paper p="md" withBorder>
              <Stack gap="md">
                <Title order={4}>Contact Information</Title>
                <FormInput
                  name="contact_email"
                  control={form.control}
                  label="Contact Email"
                  description="Contact email address to display on the site"
                  type="email"
                />
                <FormInput
                  name="contact_phone"
                  control={form.control}
                  label="Contact Phone"
                  description="Contact phone number"
                  maxLength={20}
                />
                <FormInput
                  name="website_url"
                  control={form.control}
                  label="Website URL"
                  description="Main website URL for your organization"
                  type="url"
                />
              </Stack>
            </Paper>

            <Group justify="flex-end">
              <Button type="submit" loading={saving} size="md">
                Save Settings
              </Button>
            </Group>
          </Stack>
        </form>
      </Stack>
    </div>
  );
};

export default SiteSettingsPage;
