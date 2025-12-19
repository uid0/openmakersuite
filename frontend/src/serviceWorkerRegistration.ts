/**
 * Service Worker Registration
 * Handles service worker registration and update notifications
 */

export interface ServiceWorkerRegistrationConfig {
  onUpdate?: (registration: ServiceWorkerRegistration) => void;
  onSuccess?: (registration: ServiceWorkerRegistration) => void;
  onError?: (error: Error) => void;
}

export function register(config?: ServiceWorkerRegistrationConfig) {
  if ('serviceWorker' in navigator) {
    // Service worker is supported
    const publicUrl = new URL(process.env.PUBLIC_URL || '', window.location.href);
    
    // Service worker won't work if PUBLIC_URL is on a different origin
    if (publicUrl.origin !== window.location.origin) {
      return;
    }

    window.addEventListener('load', () => {
      // Use relative path since service worker is in public folder
      const swUrl = process.env.PUBLIC_URL ? `${process.env.PUBLIC_URL}/sw.js` : '/sw.js';

      if (isLocalhost) {
        // Running on localhost - check if service worker still exists
        checkValidServiceWorker(swUrl, config);
      } else {
        // Not localhost - register service worker
        registerValidSW(swUrl, config);
      }
    });
  }
}

function registerValidSW(swUrl: string, config?: ServiceWorkerRegistrationConfig) {
  navigator.serviceWorker
    .register(swUrl)
    .then((registration) => {
      registration.onupdatefound = () => {
        const installingWorker = registration.installing;
        if (installingWorker == null) {
          return;
        }
        installingWorker.onstatechange = () => {
          if (installingWorker.state === 'installed') {
            if (navigator.serviceWorker.controller) {
              // New content available - notify user
              console.log(
                'New content is available and will be used when all ' +
                  'tabs for this page are closed.'
              );
              if (config && config.onUpdate) {
                config.onUpdate(registration);
              }
            } else {
              // Content cached for offline use
              console.log('Content cached for offline use.');
              if (config && config.onSuccess) {
                config.onSuccess(registration);
              }
            }
          }
        };
      };
    })
    .catch((error) => {
      console.error('Error during service worker registration:', error);
      if (config && config.onError) {
        config.onError(error);
      }
    });
}

function checkValidServiceWorker(swUrl: string, config?: ServiceWorkerRegistrationConfig) {
  // Check if the service worker can be found
  fetch(swUrl, {
    headers: { 'Service-Worker': 'script' },
  })
    .then((response) => {
      // Ensure service worker exists, and that we really are getting a JS file
      const contentType = response.headers.get('content-type');
      if (
        response.status === 404 ||
        (contentType != null && contentType.indexOf('javascript') === -1)
      ) {
        // No service worker found - unregister all
        navigator.serviceWorker.ready.then((registration) => {
          registration.unregister();
        });
      } else {
        // Service worker found - proceed with registration
        registerValidSW(swUrl, config);
      }
    })
    .catch(() => {
      console.log('No internet connection found. App is running in offline mode.');
    });
}

export function unregister() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.ready
      .then((registration) => {
        registration.unregister();
      })
      .catch((error) => {
        console.error(error.message);
      });
  }
}

// Check if we're running on localhost
const isLocalhost =
  window.location.hostname === 'localhost' ||
  window.location.hostname === '[::1]' ||
  window.location.hostname.match(/^127(?:\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)){3}$/);
