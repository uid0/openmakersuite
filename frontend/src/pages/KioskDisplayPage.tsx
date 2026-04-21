/**
 * Kiosk display page — rendered on the screen endpoint itself
 * (Chromecast / Raspberry Pi kiosk browser).
 *
 * This page polls the backend on an interval for content, rotates through
 * the configured blocks, and posts periodic heartbeats so operators can
 * tell the device is alive.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { kioskAPI } from '../services/api';
import { KioskPayload, ScreenBlockType, SystemMessageLevel } from '../types';

const levelColor = (level: SystemMessageLevel): string => {
  switch (level) {
    case 'critical':
      return '#c62828';
    case 'warning':
      return '#ef6c00';
    default:
      return '#1565c0';
  }
};

const blockTypeLabel = (t: ScreenBlockType): string => {
  switch (t) {
    case 'tool_status':
      return 'Tool Status';
    case 'asset_usage':
      return 'Asset Usage';
    case 'announcement':
      return 'Announcement';
    case 'weather':
      return 'Weather';
    case 'traffic':
      return 'Traffic';
    case 'member_count':
      return 'Member Count';
    case 'board_meeting':
      return 'Next Board Meeting';
    default:
      return '';
  }
};

const KioskDisplayPage: React.FC = () => {
  const { slug } = useParams<{ slug: string }>();
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [payload, setPayload] = useState<KioskPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeSlide, setActiveSlide] = useState(0);

  const fetchPayload = useCallback(async () => {
    if (!slug || !token) {
      setError('Missing screen slug or access token.');
      return;
    }
    try {
      const resp = await kioskAPI.fetchPayload(slug, token);
      setPayload(resp.data);
      setError(null);
    } catch (err) {
      console.error('Kiosk fetch failed', err);
      setError('Unable to load screen payload.');
    }
  }, [slug, token]);

  const sendHeartbeat = useCallback(async () => {
    if (!slug || !token) return;
    try {
      await kioskAPI.heartbeat(slug, token, payload?.generated_at);
    } catch (err) {
      // Heartbeats are best-effort.
      console.warn('Heartbeat failed', err);
    }
  }, [slug, token, payload?.generated_at]);

  useEffect(() => {
    fetchPayload();
  }, [fetchPayload]);

  useEffect(() => {
    if (!payload) return;
    const refreshMs = (payload.screen.refresh_interval_seconds || 60) * 1000;
    const id = window.setInterval(fetchPayload, refreshMs);
    return () => window.clearInterval(id);
  }, [payload, fetchPayload]);

  useEffect(() => {
    if (!payload) return;
    const rotationMs = (payload.screen.rotation_interval_seconds || 15) * 1000;
    const blocks = payload.content_blocks.length;
    if (blocks === 0) return;
    const id = window.setInterval(() => {
      setActiveSlide((current) => (current + 1) % blocks);
    }, rotationMs);
    return () => window.clearInterval(id);
  }, [payload]);

  useEffect(() => {
    if (!payload) return;
    sendHeartbeat();
    const id = window.setInterval(sendHeartbeat, 60000);
    return () => window.clearInterval(id);
  }, [payload, sendHeartbeat]);

  if (error) {
    return (
      <div
        data-testid="kiosk-error"
        style={{
          height: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#000',
          color: '#fff',
          fontSize: 32,
          padding: 40,
          textAlign: 'center',
        }}
      >
        {error}
      </div>
    );
  }

  if (!payload) {
    return (
      <div
        data-testid="kiosk-loading"
        style={{
          height: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#111',
          color: '#fff',
          fontSize: 32,
        }}
      >
        Loading…
      </div>
    );
  }

  const activeBlock = payload.content_blocks[activeSlide] ?? null;

  return (
    <div
      data-testid="kiosk-root"
      style={{
        minHeight: '100vh',
        background: '#0b0f19',
        color: '#f1f5f9',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {payload.system_messages.length > 0 && (
        <div data-testid="system-messages" style={{ padding: '12px 24px' }}>
          {payload.system_messages.map((m) => (
            <div
              key={m.id}
              style={{
                background: levelColor(m.level),
                color: '#fff',
                padding: '10px 16px',
                margin: '4px 0',
                borderRadius: 6,
                fontSize: 22,
                fontWeight: 600,
              }}
            >
              <span style={{ textTransform: 'uppercase', marginRight: 12 }}>{m.level}</span>
              <span>{m.title}</span>
              <div style={{ fontSize: 16, fontWeight: 400 }}>{m.body}</div>
            </div>
          ))}
        </div>
      )}

      <div
        data-testid="kiosk-slide"
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 40,
          textAlign: 'center',
        }}
      >
        {activeBlock ? (
          <>
            <div style={{ fontSize: 22, opacity: 0.7, marginBottom: 8 }}>
              {blockTypeLabel(activeBlock.block_type)}
            </div>
            {activeBlock.title && (
              <div style={{ fontSize: 72, fontWeight: 700, marginBottom: 16 }}>
                {activeBlock.title}
              </div>
            )}
            {activeBlock.body && (
              <div style={{ fontSize: 36, whiteSpace: 'pre-wrap', maxWidth: 960 }}>
                {activeBlock.body}
              </div>
            )}
          </>
        ) : (
          <div style={{ fontSize: 48, opacity: 0.7 }}>{payload.screen.name}</div>
        )}
      </div>

      <div
        style={{
          padding: '12px 24px',
          borderTop: '1px solid #1e293b',
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 16,
          opacity: 0.7,
        }}
      >
        <span>{payload.screen.name}</span>
        <span>
          Slide {Math.min(activeSlide + 1, payload.content_blocks.length || 1)} /{' '}
          {payload.content_blocks.length || 1}
        </span>
        <span>Updated {new Date(payload.generated_at).toLocaleTimeString()}</span>
      </div>
    </div>
  );
};

export default KioskDisplayPage;
