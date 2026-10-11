import React from 'react';
import {Composition} from 'remotion';
import {totalFrames} from './beats';
import {Spike} from './Spike';
import {FPS, HEIGHT, WIDTH} from './theme';

export const Root: React.FC = () => (
  <Composition
    id="Spike"
    component={Spike}
    width={WIDTH}
    height={HEIGHT}
    fps={FPS}
    durationInFrames={totalFrames}
  />
);
