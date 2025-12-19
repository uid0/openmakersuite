/**
 * Custom hook for PWA install prompt
 * Detects when the app is installable and handles the beforeinstallprompt event
 */

import { useEffect, useState } from 'react';

export interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

export interface UsePWAInstallReturn {
  isInstallable: boolean;
  isInstalled: boolean;
  installPrompt: BeforeInstallPromptEvent | null;
  showInstallPrompt: () => Promise<boolean>;
  dismiss: () => void;
  dismissed: boolean;
}

const DISMISSED_KEY = 'pwa-install-dismissed';
const DISMISSED_DURATION = 7 * 24 * 60 * 60 * 1000; // 7 days

export function usePWAInstall(): UsePWAInstallReturn {
  const [installPrompt, setInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [isInstalled, setIsInstalled] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    // Check if app is already installed
    if (window.matchMedia('(display-mode: standalone)').matches) {
      setIsInstalled(true);
      return;
    }

    // Check if user has dismissed the prompt recently
    const dismissedTimestamp = localStorage.getItem(DISMISSED_KEY);
    if (dismissedTimestamp) {
      const dismissedDate = parseInt(dismissedTimestamp, 10);
      const now = Date.now();
      if (now - dismissedDate < DISMISSED_DURATION) {
        setDismissed(true);
      } else {
        // Expired - remove from storage
        localStorage.removeItem(DISMISSED_KEY);
      }
    }

    // Listen for the beforeinstallprompt event
    const handleBeforeInstallPrompt = (e: Event) => {
      // Prevent the default mini-infobar from appearing
      e.preventDefault();
      // Store the event for later use
      setInstallPrompt(e as BeforeInstallPromptEvent);
    };

    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt);

    // Check if app was installed
    const handleAppInstalled = () => {
      setIsInstalled(true);
      setInstallPrompt(null);
      localStorage.removeItem(DISMISSED_KEY);
    };

    window.addEventListener('appinstalled', handleAppInstalled);

    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
      window.removeEventListener('appinstalled', handleAppInstalled);
    };
  }, []);

  const showInstallPrompt = async (): Promise<boolean> => {
    if (!installPrompt) {
      return false;
    }

    try {
      // Show the install prompt
      await installPrompt.prompt();

      // Wait for the user to respond
      const { outcome } = await installPrompt.userChoice;

      if (outcome === 'accepted') {
        setIsInstalled(true);
        setInstallPrompt(null);
        localStorage.removeItem(DISMISSED_KEY);
        return true;
      } else {
        // User dismissed - store dismissal timestamp
        localStorage.setItem(DISMISSED_KEY, Date.now().toString());
        setDismissed(true);
        return false;
      }
    } catch (error) {
      console.error('Error showing install prompt:', error);
      return false;
    }
  };

  const dismiss = () => {
    // Store dismissal timestamp
    localStorage.setItem(DISMISSED_KEY, Date.now().toString());
    setDismissed(true);
  };

  return {
    isInstallable: installPrompt !== null && !isInstalled && !dismissed,
    isInstalled,
    installPrompt,
    showInstallPrompt,
    dismiss,
    dismissed,
  };
}
