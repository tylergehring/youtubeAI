import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {HEIGHT, WIDTH} from '../theme';

export const Background: React.FC = () => {
  const frame = useCurrentFrame();
  const drift = (frame * 0.5) % 80;
  return (
    <AbsoluteFill
      style={{
        background:
          'radial-gradient(1300px 800px at 72% 18%, #1d2d5c 0%, transparent 62%), linear-gradient(160deg, #0a0f1f, #0d1530 60%, #080c1a)',
      }}
    >
      <svg width={WIDTH} height={HEIGHT} style={{position: 'absolute', opacity: 0.16}}>
        <defs>
          <pattern
            id="grid"
            width="80"
            height="80"
            patternUnits="userSpaceOnUse"
            patternTransform={`translate(${drift} ${drift})`}
          >
            <path d="M80 0H0V80" fill="none" stroke="#6272b5" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
      </svg>
      <AbsoluteFill
        style={{
          background:
            'radial-gradient(ellipse at center, transparent 52%, rgba(0,0,0,0.6) 100%)',
        }}
      />
    </AbsoluteFill>
  );
};
