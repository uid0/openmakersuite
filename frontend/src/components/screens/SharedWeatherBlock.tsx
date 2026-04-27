import React from 'react';

interface SharedWeatherBlockProps {
  weatherUrl: string;
}

const SharedWeatherBlock: React.FC<SharedWeatherBlockProps> = ({ weatherUrl }) => {
  if (!weatherUrl) {
    return (
      <div data-testid="shared-weather-empty" style={{ fontSize: 32, opacity: 0.7 }}>
        Weather unavailable
      </div>
    );
  }

  return (
    <iframe
      data-testid="shared-weather-iframe"
      title="Shared Weather"
      src={weatherUrl}
      style={{
        width: '100%',
        height: '100%',
        minHeight: '70vh',
        border: 0,
        background: '#fff',
      }}
    />
  );
};

export default SharedWeatherBlock;
