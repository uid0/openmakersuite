/**
 * QR Scanner Component
 * Mobile-optimized QR code scanner using html5-qrcode
 */
import { Html5Qrcode } from 'html5-qrcode';
import React, { useEffect, useRef, useState } from 'react';
import '../styles/QRScanner.css';

interface QRScannerProps {
  onScanSuccess: (decodedText: string) => void;
  onScanError?: (error: string) => void;
  onClose?: () => void;
  fps?: number;
  qrbox?: { width: number; height: number };
  aspectRatio?: number;
}

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
  const [error, setError] = useState<string | null>(null);
  const [torchEnabled, setTorchEnabled] = useState(false);
  const [hasMultipleCameras, setHasMultipleCameras] = useState(false);
  const [currentCameraId, setCurrentCameraId] = useState<string | null>(null);
  const [availableCameras, setAvailableCameras] = useState<MediaDeviceInfo[]>([]);
  const scannerIdRef = useRef(`qr-scanner-${Date.now()}-${Math.random()}`);

  // Detect available cameras
  useEffect(() => {
    const detectCameras = async () => {
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const videoDevices = devices.filter((device) => device.kind === 'videoinput');
        setAvailableCameras(videoDevices);
        setHasMultipleCameras(videoDevices.length > 1);
        if (videoDevices.length > 0) {
          setCurrentCameraId(videoDevices[0].deviceId);
        }
      } catch (err) {
        console.error('Error detecting cameras:', err);
      }
    };
    detectCameras();
  }, []);

  // Start scanning
  useEffect(() => {
    if (!scannerRef.current || !currentCameraId) return;

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
                onScanError(errorMessage);
              }
            }
          }
        );

        setIsScanning(true);
        setError(null);
      } catch (err: any) {
        const errorMsg = err.message || 'Failed to start camera';
        setError(errorMsg);
        setIsScanning(false);
        if (onScanError) {
          onScanError(errorMsg);
        }
      }
    };

    startScanning();

    return () => {
      stopScanning();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentCameraId, fps, aspectRatio, onScanSuccess, onScanError]);

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
        <div className="qr-scanner-error">
          <p>{error}</p>
          <button onClick={() => setError(null)}>Dismiss</button>
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
