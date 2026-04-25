/**
 * QR Scanner Component
 * Mobile-optimized QR code scanner using html5-qrcode
 */
import { Html5Qrcode } from 'html5-qrcode';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import '../styles/QRScanner.css';

export type QRScannerErrorKind =
  | 'permission-denied'
  | 'no-camera'
  | 'insecure-context'
  | 'unsupported'
  | 'unknown';

export interface QRScannerError {
  kind: QRScannerErrorKind;
  message: string;
}

interface QRScannerProps {
  onScanSuccess: (decodedText: string) => void;
  onScanError?: (error: QRScannerError) => void;
  onClose?: () => void;
  fps?: number;
  qrbox?: { width: number; height: number };
  aspectRatio?: number;
}

const PERMISSION_DENIED_MESSAGE =
  'Camera access denied. Enable camera permission for this site in your browser settings, then tap "Try again".';
const NO_CAMERA_MESSAGE =
  'No camera detected on this device. Connect a camera and reload, or enter the code manually.';
const INSECURE_CONTEXT_MESSAGE =
  'Camera access requires HTTPS. Open this page over https:// (or http://localhost) and try again.';
const UNSUPPORTED_MESSAGE =
  'This browser does not support camera access. Try Chrome, Safari, or Firefox.';

const classifyMediaError = (err: unknown): QRScannerError => {
  const name = (err as { name?: string } | null)?.name;
  const fallback = (err as { message?: string } | null)?.message;
  switch (name) {
    case 'NotAllowedError':
    case 'SecurityError':
    case 'PermissionDeniedError':
      return { kind: 'permission-denied', message: PERMISSION_DENIED_MESSAGE };
    case 'NotFoundError':
    case 'OverconstrainedError':
    case 'DevicesNotFoundError':
      return { kind: 'no-camera', message: NO_CAMERA_MESSAGE };
    case 'NotSupportedError':
    case 'TypeError':
      return { kind: 'unsupported', message: UNSUPPORTED_MESSAGE };
    default:
      return { kind: 'unknown', message: fallback || 'Failed to start camera.' };
  }
};

const QRScanner: React.FC<QRScannerProps> = ({
  onScanSuccess,
  onScanError,
  onClose,
  fps = 10,
  qrbox = { width: 250, height: 250 },
  aspectRatio = 1.0,
}) => {
  const scannerRef = useRef<HTMLDivElement>(null);
  const html5QrCodeRef = useRef<Html5Qrcode | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<QRScannerError | null>(null);
  const [torchEnabled, setTorchEnabled] = useState(false);
  const [hasMultipleCameras, setHasMultipleCameras] = useState(false);
  const [currentCameraId, setCurrentCameraId] = useState<string | null>(null);
  const [availableCameras, setAvailableCameras] = useState<MediaDeviceInfo[]>([]);
  const [permissionGranted, setPermissionGranted] = useState(false);
  const [retryToken, setRetryToken] = useState(0);
  const scannerIdRef = useRef(`qr-scanner-${Date.now()}-${Math.random()}`);

  const reportError = useCallback(
    (qrError: QRScannerError) => {
      setError(qrError);
      setIsScanning(false);
      if (onScanError) {
        onScanError(qrError);
      }
    },
    [onScanError]
  );

  // Explicit getUserMedia preflight to force the browser permission prompt.
  // Without this, html5-qrcode swallows the DOMException and surfaces a
  // generic "Failed to start camera" instead of letting the browser ask the
  // user. We request the stream, then immediately stop tracks so html5-qrcode
  // can take its own stream when it starts.
  useEffect(() => {
    let cancelled = false;

    const requestPermission = async () => {
      if (
        typeof window === 'undefined' ||
        !window.isSecureContext ||
        !navigator.mediaDevices?.getUserMedia
      ) {
        if (typeof window !== 'undefined' && window.isSecureContext === false) {
          reportError({ kind: 'insecure-context', message: INSECURE_CONTEXT_MESSAGE });
        } else {
          reportError({ kind: 'unsupported', message: UNSUPPORTED_MESSAGE });
        }
        return;
      }

      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'environment' },
        });
        stream.getTracks().forEach((track) => track.stop());
        if (cancelled) return;
        setPermissionGranted(true);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        reportError(classifyMediaError(err));
      }
    };

    requestPermission();

    return () => {
      cancelled = true;
    };
  }, [retryToken, reportError]);

  // Detect available cameras (only after permission so labels populate)
  useEffect(() => {
    if (!permissionGranted) return;
    let cancelled = false;
    const detectCameras = async () => {
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        if (cancelled) return;
        const videoDevices = devices.filter((device) => device.kind === 'videoinput');
        setAvailableCameras(videoDevices);
        setHasMultipleCameras(videoDevices.length > 1);
        if (videoDevices.length === 0) {
          reportError({ kind: 'no-camera', message: NO_CAMERA_MESSAGE });
          return;
        }
        setCurrentCameraId((prev) => prev ?? videoDevices[0].deviceId);
      } catch (err) {
        if (cancelled) return;
        reportError(classifyMediaError(err));
      }
    };
    detectCameras();
    return () => {
      cancelled = true;
    };
  }, [permissionGranted, reportError]);

  // Start scanning
  useEffect(() => {
    if (!scannerRef.current || !currentCameraId || !permissionGranted) return;

    const startScanning = async () => {
      try {
        const scannerId = scannerIdRef.current;
        if (scannerRef.current) {
          scannerRef.current.id = scannerId;
        }
        const html5QrCode = new Html5Qrcode(scannerId);
        html5QrCodeRef.current = html5QrCode;

        await html5QrCode.start(
          currentCameraId,
          {
            fps,
            qrbox: (width, height) => {
              // Use responsive qrbox on mobile
              const isMobile = window.innerWidth < 768;
              if (isMobile) {
                const size = Math.min(width * 0.8, height * 0.6, 300);
                return { width: size, height: size };
              }
              return qrbox;
            },
            aspectRatio,
          },
          (decodedText) => {
            // Success callback
            onScanSuccess(decodedText);
            // Stop scanning after successful scan
            stopScanning();
          },
          (errorMessage) => {
            // Error callback - ignore most errors as they're just "no QR code found"
            if (errorMessage && !errorMessage.includes('No QR code found')) {
              if (onScanError) {
                onScanError({ kind: 'unknown', message: errorMessage });
              }
            }
          }
        );

        setIsScanning(true);
        setError(null);
      } catch (err) {
        reportError(classifyMediaError(err));
      }
    };

    startScanning();

    return () => {
      stopScanning();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentCameraId, permissionGranted, fps, aspectRatio]);

  const handleRetry = () => {
    setError(null);
    setPermissionGranted(false);
    setCurrentCameraId(null);
    setRetryToken((token) => token + 1);
  };

  const stopScanning = async () => {
    if (html5QrCodeRef.current && isScanning) {
      try {
        await html5QrCodeRef.current.stop();
        await html5QrCodeRef.current.clear();
        html5QrCodeRef.current = null;
        setIsScanning(false);
      } catch (err) {
        console.error('Error stopping scanner:', err);
      }
    }
  };

  const toggleTorch = async () => {
    if (!html5QrCodeRef.current || !currentCameraId) return;

    try {
      const capabilities = html5QrCodeRef.current.getRunningTrackCapabilities();
      // Type assertion needed as torch is not in standard MediaTrackCapabilities type
      const torchCapable = capabilities && (capabilities as any).torch === true;
      if (torchCapable) {
        await html5QrCodeRef.current.applyVideoConstraints({
          advanced: [{ torch: !torchEnabled } as any],
        });
        setTorchEnabled(!torchEnabled);
      }
    } catch (err) {
      console.error('Error toggling torch:', err);
    }
  };

  const switchCamera = async () => {
    if (availableCameras.length < 2) return;

    const currentIndex = availableCameras.findIndex(
      (cam) => cam.deviceId === currentCameraId
    );
    const nextIndex = (currentIndex + 1) % availableCameras.length;
    const nextCameraId = availableCameras[nextIndex].deviceId;

    await stopScanning();
    setCurrentCameraId(nextCameraId);
  };

  const handleClose = () => {
    stopScanning();
    if (onClose) {
      onClose();
    }
  };

  return (
    <div className="qr-scanner-container">
      <div className="qr-scanner-header">
        <h2>Scan QR Code</h2>
        <button
          className="qr-scanner-close"
          onClick={handleClose}
          aria-label="Close scanner"
        >
          ✕
        </button>
      </div>

      {error && (
        <div
          className="qr-scanner-error"
          role="alert"
          data-error-kind={error.kind}
        >
          <p>{error.message}</p>
          <div className="qr-scanner-error-actions">
            <button onClick={handleRetry}>Try again</button>
            <button onClick={handleClose}>Close</button>
          </div>
        </div>
      )}

      <div className="qr-scanner-viewport">
        <div ref={scannerRef} className="qr-scanner-element" />
        {!isScanning && !error && (
          <div className="qr-scanner-loading">
            <p>Starting camera...</p>
          </div>
        )}
      </div>

      <div className="qr-scanner-controls">
        {hasMultipleCameras && (
          <button
            className="qr-scanner-button"
            onClick={switchCamera}
            aria-label="Switch camera"
            title="Switch camera"
          >
            🔄 Switch Camera
          </button>
        )}
        <button
          className="qr-scanner-button"
          onClick={toggleTorch}
          aria-label="Toggle flashlight"
          title="Toggle flashlight"
        >
          {torchEnabled ? '💡 Flashlight On' : '🔦 Flashlight Off'}
        </button>
        <button
          className="qr-scanner-button"
          onClick={handleClose}
          aria-label="Close scanner"
        >
          Cancel
        </button>
      </div>

      <div className="qr-scanner-instructions">
        <p>Position the QR code within the frame</p>
      </div>
    </div>
  );
};

export default QRScanner;
