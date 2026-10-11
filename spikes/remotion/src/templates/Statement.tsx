import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {colors, fontFamily} from '../theme';

export type StatementProps = {
  lines: {text: string; accent?: string[]}[];
};

const norm = (w: string) => w.toLowerCase().replace(/[^a-z0-9$%.]/g, '');

// Big centred statement; words pop in one by one, accent words are highlighted.
export const Statement: React.FC<StatementProps> = ({lines}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  let wordIndex = 0;

  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', fontFamily}}>
      <div style={{textAlign: 'center', paddingBottom: 120}}>
        {lines.map((line, li) => {
          const accent = new Set((line.accent ?? []).flatMap((a) => a.split(' ').map(norm)));
          return (
            <div key={li} style={{fontSize: 150, fontWeight: 900, lineHeight: 1.12, letterSpacing: -3}}>
              {line.text.split(' ').map((word, wi) => {
                const i = wordIndex++;
                const s = spring({frame: frame - 4 - i * 4, fps, config: {damping: 13, stiffness: 150}});
                const isAccent = accent.has(norm(word));
                return (
                  <span
                    key={wi}
                    style={{
                      display: 'inline-block',
                      marginRight: 30,
                      opacity: interpolate(s, [0, 0.6], [0, 1], {extrapolateRight: 'clamp'}),
                      transform: `translateY(${(1 - s) * 50}px) scale(${0.85 + 0.15 * s})`,
                      color: isAccent ? colors.accent : colors.text,
                      textShadow: isAccent ? '0 0 50px rgba(255,184,77,0.45)' : 'none',
                    }}
                  >
                    {word}
                  </span>
                );
              })}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
