import React from 'react';

interface SharedTrafficBlockProps {
  url: string;
  title?: string;
}

const SharedTrafficBlock: React.FC<SharedTrafficBlockProps> = ({ url, title }) => {
  return (
    <div
      data-testid="shared-traffic-block"
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'stretch',
      }}
    >
      {title && (
        <div style={{ fontSize: 48, fontWeight: 700, marginBottom: 12, textAlign: 'center' }}>
          {title}
        </div>
      )}
      <iframe
        data-testid="shared-traffic-iframe"
        title="Shared traffic map"
        src={url}
        style={{
          flex: 1,
          width: '100%',
          minHeight: 480,
          border: 'none',
          background: '#000',
        }}
        allow="geolocation"
      />
    </div>
  );
};

export default SharedTrafficBlock;
