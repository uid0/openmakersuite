/**
 * NFPADiamond - Visual display of NFPA Fire Diamond
 * Displays the four-quadrant hazard diamond with color-coded ratings
 */
import { Box, Stack, Text } from '@mantine/core';
import React from 'react';

export interface NFPADiamondProps {
  health?: number | null;
  flammability?: number | null;
  instability?: number | null;
  special?: string;
  size?: number;
}

// NFPA color scheme: 0=white, 1=blue, 2=yellow, 3=orange, 4=red
const getColor = (rating: number | null | undefined): string => {
  if (rating === null || rating === undefined) return '#E0E0E0'; // Gray for missing
  switch (rating) {
    case 0:
      return '#FFFFFF'; // White
    case 1:
      return '#0066CC'; // Blue
    case 2:
      return '#FFCC00'; // Yellow
    case 3:
      return '#FF6600'; // Orange
    case 4:
      return '#FF0000'; // Red
    default:
      return '#E0E0E0'; // Gray
  }
};

const getTextColor = (rating: number | null | undefined): string => {
  if (rating === null || rating === undefined) return '#666';
  if (rating === 0 || rating === 1) return '#000000';
  return '#000000';
};

export const NFPADiamond: React.FC<NFPADiamondProps> = ({
  health,
  flammability,
  instability,
  special = '',
  size = 120,
}) => {
  const halfSize = size / 2;
  const fontSize = size / 6;
  const labelSize = size / 8;

  return (
    <Stack gap="xs" align="center">
      <Box
        style={{
          position: 'relative',
          width: size,
          height: size,
          transform: 'rotate(45deg)',
          border: '2px solid #000',
        }}
      >
        {/* Health (left) */}
        <Box
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: halfSize,
            height: halfSize,
            backgroundColor: getColor(health),
            borderRight: '1px solid #000',
            borderBottom: '1px solid #000',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text
            style={{
              transform: 'rotate(-45deg)',
              fontSize: `${fontSize}px`,
              fontWeight: 'bold',
              color: getTextColor(health),
            }}
          >
            {health !== null && health !== undefined ? health : '?'}
          </Text>
        </Box>

        {/* Flammability (top) */}
        <Box
          style={{
            position: 'absolute',
            left: halfSize,
            top: 0,
            width: halfSize,
            height: halfSize,
            backgroundColor: getColor(flammability),
            borderBottom: '1px solid #000',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text
            style={{
              transform: 'rotate(-45deg)',
              fontSize: `${fontSize}px`,
              fontWeight: 'bold',
              color: getTextColor(flammability),
            }}
          >
            {flammability !== null && flammability !== undefined ? flammability : '?'}
          </Text>
        </Box>

        {/* Instability (right) */}
        <Box
          style={{
            position: 'absolute',
            left: halfSize,
            top: halfSize,
            width: halfSize,
            height: halfSize,
            backgroundColor: getColor(instability),
            borderLeft: '1px solid #000',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text
            style={{
              transform: 'rotate(-45deg)',
              fontSize: `${fontSize}px`,
              fontWeight: 'bold',
              color: getTextColor(instability),
            }}
          >
            {instability !== null && instability !== undefined ? instability : '?'}
          </Text>
        </Box>

        {/* Special Hazards (bottom) */}
        <Box
          style={{
            position: 'absolute',
            left: 0,
            top: halfSize,
            width: halfSize,
            height: halfSize,
            backgroundColor: '#FFFFFF',
            borderRight: '1px solid #000',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexDirection: 'column',
          }}
        >
          <Text
            style={{
              transform: 'rotate(-45deg)',
              fontSize: `${labelSize}px`,
              fontWeight: 'bold',
              color: '#000',
              textAlign: 'center',
              lineHeight: 1,
            }}
          >
            {special || ''}
          </Text>
        </Box>
      </Box>

      {/* Labels */}
      <Stack gap={2} style={{ fontSize: `${labelSize}px` }}>
        <Text size="xs" style={{ textAlign: 'center' }}>
          <strong>Health:</strong> {health !== null && health !== undefined ? health : 'N/A'} |{' '}
          <strong>Fire:</strong> {flammability !== null && flammability !== undefined ? flammability : 'N/A'} |{' '}
          <strong>Instability:</strong> {instability !== null && instability !== undefined ? instability : 'N/A'}
        </Text>
        {special && (
          <Text size="xs" style={{ textAlign: 'center' }}>
            <strong>Special:</strong> {special}
          </Text>
        )}
      </Stack>
    </Stack>
  );
};

export default NFPADiamond;
