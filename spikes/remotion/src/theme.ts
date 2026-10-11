import {loadFont} from '@remotion/google-fonts/Inter';

export const {fontFamily} = loadFont('normal', {
  weights: ['500', '700', '800', '900'],
  subsets: ['latin'],
});

export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;

export const colors = {
  text: '#f5f7ff',
  muted: '#8f9bc7',
  accent: '#ffb84d', // amber: emphasis
  danger: '#ff5d73', // red: costs rising / bad
  calm: '#6b7fd1', // blue: neutral data
  good: '#4ade80',
};

export const fmt = (n: number, decimals = 0) =>
  n.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
