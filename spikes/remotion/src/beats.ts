import type {BarChartProps} from './templates/BarChart';
import type {BigNumberProps} from './templates/BigNumber';
import type {StatementProps} from './templates/Statement';
import {FPS} from './theme';

// A beat is one sentence of narration plus the visual template that illustrates it.
// This is the shape our scene planner would eventually emit (as JSON) instead of free-form scenes.
export type Visual =
  | ({template: 'statement'} & StatementProps)
  | ({template: 'bigNumber'} & BigNumberProps)
  | ({template: 'barChart'} & BarChartProps);

export type Beat = {
  id: string;
  durationSec: number;
  narration: string;
  visual: Visual;
};

// Numbers come from the facts extracted from the sample transcript (see knowledge.json).
export const beats: Beat[] = [
  {
    id: 'hook',
    durationSec: 5.2,
    narration: "Americans are running out of money, and it's hitting people you wouldn't expect.",
    visual: {
      template: 'statement',
      lines: [
        {text: 'Americans are'},
        {text: 'running out of money.', accent: ['running out of money.']},
      ],
    },
  },
  {
    id: 'cost-of-living',
    durationSec: 5.8,
    narration: 'A family of four now needs nearly $140,000 a year just to get by.',
    visual: {
      template: 'bigNumber',
      prefix: '$',
      value: 140000,
      label: 'a year for a family of four to get by',
      color: '#ffb84d',
    },
  },
  {
    id: 'savings',
    durationSec: 5.8,
    narration: 'Meanwhile, the savings rate has fallen to just 3%. Close to a record low.',
    visual: {
      template: 'bigNumber',
      value: 3,
      suffix: '%',
      label: 'personal savings rate',
      color: '#ff5d73',
      meter: {max: 10, unit: '%', caption: 'of income saved'},
    },
  },
  {
    id: 'car-payment',
    durationSec: 5.0,
    narration: 'The average new car payment just hit a record: $765 a month.',
    visual: {
      template: 'bigNumber',
      prefix: '$',
      value: 765,
      suffix: '/mo',
      label: 'average new car payment, a record',
      color: '#ffb84d',
      car: true,
    },
  },
  {
    id: 'prices-vs-wages',
    durationSec: 8.0,
    narration:
      'Wages rose 4.1%. Airfare jumped 23.4% and used cars 8.2%, while rent, groceries and eating out rose less.',
    visual: {
      template: 'barChart',
      title: 'Price increases vs. wages',
      unit: '%',
      max: 25,
      bars: [
        {label: 'Airfare', value: 23.4, note: 'last year'},
        {label: 'Used cars', value: 8.2, note: 'since 2024'},
        {label: 'Eating out', value: 3.4},
        {label: 'Rent', value: 2.5},
        {label: 'Groceries', value: 2.2},
      ],
      reference: {label: 'Wages +4.1%', value: 4.1},
      legend: 'Red = rising faster than wages',
    },
  },
  {
    id: 'close',
    durationSec: 5.0,
    narration: "Even households earning $100,000 or more say they're feeling the squeeze.",
    visual: {
      template: 'statement',
      lines: [
        {text: 'Even $100K households', accent: ['$100K']},
        {text: 'are feeling it.'},
      ],
    },
  },
];

export const totalFrames = Math.round(beats.reduce((s, b) => s + b.durationSec, 0) * FPS);

// ---- word timing -----------------------------------------------------------
// Spike only: spread words across each beat by length. The real pipeline will take word times
// from forced alignment of the actual narration audio.

export type TimedWord = {text: string; start: number; end: number};

const LEAD_SEC = 0.35;
const TAIL_SEC = 0.35;

export const timedWords: TimedWord[] = (() => {
  const out: TimedWord[] = [];
  let beatStart = 0;
  for (const beat of beats) {
    const tokens = beat.narration.split(/\s+/);
    const weights = tokens.map((t) => t.length + 2 + (/[,.:;?!]$/.test(t) ? 5 : 0));
    const total = weights.reduce((a, b) => a + b, 0);
    const span = beat.durationSec - LEAD_SEC - TAIL_SEC;
    let cursor = beatStart + LEAD_SEC;
    tokens.forEach((text, i) => {
      const dur = (weights[i] / total) * span;
      out.push({text, start: cursor, end: cursor + dur});
      cursor += dur;
    });
    beatStart += beat.durationSec;
  }
  return out;
})();
