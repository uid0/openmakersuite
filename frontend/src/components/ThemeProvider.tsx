/**
 * Theme Provider Component
 * Wraps MantineProvider with backend-driven theme configuration
 * Handles colors, dark mode, custom CSS injection, and favicon updates
 */
import { ColorSchemeScript, MantineProvider, createTheme } from '@mantine/core';
import React, { useEffect, useMemo } from 'react';
import { useTheme } from '../contexts/ThemeContext';
import { useSiteSettings } from '../hooks/useSiteSettings';

interface ThemeProviderProps {
  children: React.ReactNode;
}

/**
 * Convert hex color to RGB values for Mantine
 */
const hexToRgb = (hex: string): [number, number, number] => {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) {
    return [0, 124, 186]; // Default blue
  }
  return [
    parseInt(result[1], 16),
    parseInt(result[2], 16),
    parseInt(result[3], 16),
  ];
};

/**
 * Generate color palette from a base color
 * Creates shades 0-9 for Mantine color system
 * Returns as tuple of 10 hex color strings
 */
const generateColorPalette = (baseHex: string): [string, string, string, string, string, string, string, string, string, string] => {
  const [r, g, b] = hexToRgb(baseHex);
  const palette: string[] = [];

  // Generate shades from lightest (0) to darkest (9)
  for (let i = 0; i < 10; i++) {
    // Interpolate between white (0) and base color (5) to base color (9)
    let newR: number;
    let newG: number;
    let newB: number;

    if (i <= 5) {
      // Light shades: interpolate from white to base
      const factor = i / 5;
      newR = Math.round(255 - (255 - r) * factor);
      newG = Math.round(255 - (255 - g) * factor);
      newB = Math.round(255 - (255 - b) * factor);
    } else {
      // Dark shades: interpolate from base to darker
      const factor = (i - 5) / 4;
      newR = Math.round(r * (1 - factor * 0.3));
      newG = Math.round(g * (1 - factor * 0.3));
      newB = Math.round(b * (1 - factor * 0.3));
    }

    // Convert to hex
    const toHex = (n: number) => {
      const hex = Math.round(n).toString(16).padStart(2, '0');
      return hex;
    };
    palette.push(`#${toHex(newR)}${toHex(newG)}${toHex(newB)}`);
  }

  return palette as [string, string, string, string, string, string, string, string, string, string];
};

export const AppThemeProvider: React.FC<ThemeProviderProps> = ({ children }) => {
  const { settings } = useSiteSettings();
  const { effectiveColorScheme } = useTheme();

  // Generate theme with backend colors
  const theme = useMemo(() => {
    const primaryColor = settings?.primary_color || '#007cba';
    const secondaryColor = settings?.secondary_color || '#417690';

    // Generate color palettes
    const primaryPalette = generateColorPalette(primaryColor);
    const secondaryPalette = generateColorPalette(secondaryColor);

    return createTheme({
      primaryColor: 'primary',
      colors: {
        primary: primaryPalette,
        secondary: secondaryPalette,
      },
      defaultRadius: 'md',
      fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    });
  }, [settings?.primary_color, settings?.secondary_color]);

  // Inject custom CSS
  useEffect(() => {
    if (!settings?.custom_css) {
      return;
    }

    // Remove existing custom CSS style tag if it exists
    const existingStyle = document.getElementById('custom-site-css');
    if (existingStyle) {
      existingStyle.remove();
    }

    // Create and inject new style tag
    const style = document.createElement('style');
    style.id = 'custom-site-css';
    style.textContent = settings.custom_css;
    document.head.appendChild(style);

    // Cleanup on unmount or when CSS changes
    return () => {
      const styleToRemove = document.getElementById('custom-site-css');
      if (styleToRemove) {
        styleToRemove.remove();
      }
    };
  }, [settings?.custom_css]);

  // Update favicon
  useEffect(() => {
    if (!settings?.favicon_url) {
      return;
    }

    // Remove existing favicon links
    const existingLinks = document.querySelectorAll('link[rel="icon"], link[rel="shortcut icon"]');
    existingLinks.forEach((link) => link.remove());

    // Add new favicon
    const link = document.createElement('link');
    link.rel = 'icon';
    link.type = 'image/png'; // Adjust if needed based on actual favicon type
    link.href = settings.favicon_url;
    document.head.appendChild(link);

    // Also add apple-touch-icon if it's a square image
    const appleLink = document.createElement('link');
    appleLink.rel = 'apple-touch-icon';
    appleLink.href = settings.favicon_url;
    document.head.appendChild(appleLink);
  }, [settings?.favicon_url]);

  return (
    <>
      <ColorSchemeScript defaultColorScheme={effectiveColorScheme} />
      <MantineProvider theme={theme} defaultColorScheme={effectiveColorScheme}>
        {children}
      </MantineProvider>
    </>
  );
};
