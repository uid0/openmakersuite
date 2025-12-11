/**
 * Theme Context
 * Provides theme (dark/light mode) functionality throughout the application
 */
import React, { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';

export type ColorScheme = 'light' | 'dark' | 'auto';

interface ThemeContextType {
  colorScheme: ColorScheme;
  effectiveColorScheme: 'light' | 'dark';
  setColorScheme: (scheme: ColorScheme) => void;
  toggleColorScheme: () => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

const STORAGE_KEY = 'theme-color-scheme';

/**
 * Detect system color scheme preference
 */
const getSystemColorScheme = (): 'light' | 'dark' => {
  if (typeof window === 'undefined') {
    return 'light';
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
};

/**
 * Get effective color scheme (resolves 'auto' to actual system preference)
 */
const getEffectiveColorScheme = (colorScheme: ColorScheme): 'light' | 'dark' => {
  if (colorScheme === 'auto') {
    return getSystemColorScheme();
  }
  return colorScheme;
};

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within ThemeProvider');
  }
  return context;
};

interface ThemeProviderProps {
  children: ReactNode;
}

export const ThemeProvider: React.FC<ThemeProviderProps> = ({ children }) => {
  // Initialize from localStorage or default to 'auto'
  const [colorScheme, setColorSchemeState] = useState<ColorScheme>(() => {
    if (typeof window === 'undefined') {
      return 'auto';
    }
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'auto') {
      return stored;
    }
    return 'auto';
  });

  const [systemColorScheme, setSystemColorScheme] = useState<'light' | 'dark'>(getSystemColorScheme);

  // Calculate effective color scheme
  const effectiveColorScheme = colorScheme === 'auto' ? systemColorScheme : colorScheme;

  // Listen for system preference changes
  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = (e: MediaQueryListEvent) => {
      setSystemColorScheme(e.matches ? 'dark' : 'light');
    };

    // Modern browsers
    if (mediaQuery.addEventListener) {
      mediaQuery.addEventListener('change', handleChange);
      return () => mediaQuery.removeEventListener('change', handleChange);
    }
    // Fallback for older browsers
    else if (mediaQuery.addListener) {
      mediaQuery.addListener(handleChange);
      return () => mediaQuery.removeListener(handleChange);
    }
  }, []);

  // Update localStorage when color scheme changes
  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem(STORAGE_KEY, colorScheme);
    }
  }, [colorScheme]);

  // Update document class for CSS-based theming
  useEffect(() => {
    if (typeof document !== 'undefined') {
      const root = document.documentElement;
      root.classList.remove('light', 'dark');
      root.classList.add(effectiveColorScheme);
    }
  }, [effectiveColorScheme]);

  const setColorScheme = useCallback((scheme: ColorScheme) => {
    setColorSchemeState(scheme);
  }, []);

  const toggleColorScheme = useCallback(() => {
    setColorSchemeState((current) => {
      if (current === 'auto') {
        // If auto, toggle to opposite of current system preference
        return systemColorScheme === 'dark' ? 'light' : 'dark';
      }
      // Toggle between light and dark
      return current === 'light' ? 'dark' : 'light';
    });
  }, [systemColorScheme]);

  const value: ThemeContextType = {
    colorScheme,
    effectiveColorScheme,
    setColorScheme,
    toggleColorScheme,
  };

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
};
