import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {TimedWord} from '../beats';
import {colors, fontFamily} from '../theme';

const CHUNK_SIZE = 5;

// Group words into short on-screen phrases, breaking after punctuation when possible.
const makeChunks = (words: TimedWord[]) => {
  const chunks: TimedWord[][] = [];
  let current: TimedWord[] = [];
  for (const w of words) {
    current.push(w);
    const endsPhrase = /[,.:;?!]$/.test(w.text);
    if (current.length >= CHUNK_SIZE || (endsPhrase && current.length >= 2)) {
      chunks.push(current);
      current = [];
    }
  }
  if (current.length) chunks.push(current);
  return chunks;
};

// Word-by-word karaoke captions: the spoken word is highlighted and pops slightly.
export const Captions: React.FC<{words: TimedWord[]}> = ({words}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const chunks = React.useMemo(() => makeChunks(words), [words]);

  const chunk = chunks.find((c, i) => {
    const start = c[0].start;
    const nextStart = chunks[i + 1]?.[0].start ?? Infinity;
    return t >= start - 0.05 && t < Math.min(c[c.length - 1].end + 0.35, nextStart);
  });
  if (!chunk) return null;

  const enter = spring({frame: frame - Math.round(chunk[0].start * fps), fps, config: {damping: 16, stiffness: 200}});

  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: 118,
        display: 'flex',
        justifyContent: 'center',
        fontFamily,
        opacity: interpolate(enter, [0, 1], [0, 1]),
        transform: `translateY(${(1 - enter) * 24}px)`,
      }}
    >
      <div
        style={{
          padding: '10px 36px',
          fontSize: 66,
          fontWeight: 800,
          lineHeight: 1.2,
          textAlign: 'center',
          maxWidth: 1600,
          WebkitTextStroke: '12px rgba(5,8,20,0.9)',
          paintOrder: 'stroke fill',
          letterSpacing: -0.5,
        }}
      >
        {chunk.map((w, i) => {
          const spoken = t >= w.start;
          const active = t >= w.start && t < w.end;
          return (
            <span
              key={i}
              style={{
                display: 'inline-block',
                marginRight: 30,
                color: active ? colors.accent : spoken ? colors.text : 'rgba(245,247,255,0.55)',
                transform: `scale(${active ? 1.06 : 1})`,
              }}
            >
              {w.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};
