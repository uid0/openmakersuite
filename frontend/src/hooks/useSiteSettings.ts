/**
 * Hook to fetch and use site customization settings
 */
import { useEffect, useState } from 'react';
import { customizationAPI } from '../services/api';
import { SiteSettings } from '../types';

interface UseSiteSettingsReturn {
  settings: SiteSettings | null;
  loading: boolean;
  error: string | null;
}

const defaultSettings: SiteSettings = {
  site_name: 'Makerspace Inventory',
  site_tagline: '',
  logo_url: null,
  logo_alt_text: 'Site Logo',
  favicon_url: null,
  primary_color: '#007cba',
  secondary_color: '#417690',
  footer_text: '',
  contact_email: '',
  contact_phone: '',
  website_url: '',
  dashboard_title: '',
  dashboard_subtitle: '',
  show_logo_on_dashboard: true,
};

export const useSiteSettings = (): UseSiteSettingsReturn => {
  const [settings, setSettings] = useState<SiteSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        setLoading(true);
        setError(null);
        const response = await customizationAPI.getSiteSettings();
        setSettings(response.data);
      } catch (err: any) {
        console.warn('Failed to fetch site settings, using defaults:', err);
        // Use defaults if API fails (e.g., during development or if settings don't exist yet)
        setSettings(defaultSettings);
        setError(err?.message || 'Failed to load site settings');
      } finally {
        setLoading(false);
      }
    };

    fetchSettings();
  }, []);

  return { settings, loading, error };
};
