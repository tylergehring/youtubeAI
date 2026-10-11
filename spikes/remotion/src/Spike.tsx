import React from 'react';
import {AbsoluteFill, interpolate, Sequence, useCurrentFrame} from 'remotion';
import {beats, timedWords, type Beat} from './beats';
import {Background} from './templates/Background';
import {BarChart} from './templates/BarChart';
import {BigNumber} from './templates/BigNumber';
import {Captions} from './templates/Captions';
import {Statement} from './templates/Statement';
import {FPS} from './theme';

const FADE = 7;

// Fades/slides each beat in and out so cuts feel intentional.
const BeatFrame: React.FC<{frames: number; children: React.ReactNode}> = ({frames, children}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, FADE, frames - FADE, frames], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const shift = interpolate(frame, [0, FADE], [14, 0], {extrapolateRight: 'clamp'});
  return <AbsoluteFill style={{opacity, transform: `translateY(${shift}px)`}}>{children}</AbsoluteFill>;
};

const renderVisual = (beat: Beat) => {
  const v = beat.visual;
  switch (v.template) {
    case 'statement':
      return <Statement {...v} />;
    case 'bigNumber':
      return <BigNumber {...v} />;
    case 'barChart':
      return <BarChart {...v} />;
  }
};

export const Spike: React.FC = () => {
  let from = 0;
  return (
    <AbsoluteFill>
      <Background />
      {beats.map((beat) => {
        const frames = Math.round(beat.durationSec * FPS);
        const node = (
          <Sequence key={beat.id} from={from} durationInFrames={frames} name={beat.id}>
            <BeatFrame frames={frames}>{renderVisual(beat)}</BeatFrame>
          </Sequence>
        );
        from += frames;
        return node;
      })}
      <Captions words={timedWords} />
    </AbsoluteFill>
  );
};
