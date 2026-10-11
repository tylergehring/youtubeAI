import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {colors, fmt, fontFamily} from '../theme';

export type BarChartProps = {
  title: string;
  unit: string;
  max: number;
  bars: {label: string; value: number; note?: string}[];
  reference: {label: string; value: number};
  legend: string;
};

const ROW = 92;
const BAR_H = 70;
const LABEL_W = 400;
const AREA_W = 1100;

// Horizontal bars grow in sequence; a reference line then sweeps in. Bars past the line turn red.
export const BarChart: React.FC<BarChartProps> = ({title, unit, max, bars, reference, legend}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sorted = [...bars].sort((a, b) => b.value - a.value);
  const refX = (reference.value / max) * AREA_W;
  const lineStart = 16 + sorted.length * 9 + 14;
  const lineIn = interpolate(frame, [lineStart, lineStart + 18], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const titleIn = spring({frame, fps, config: {damping: 16}});
  const chartH = sorted.length * ROW;

  return (
    <AbsoluteFill style={{fontFamily, alignItems: 'center'}}>
      <div
        style={{
          marginTop: 64,
          fontSize: 66,
          fontWeight: 800,
          color: colors.text,
          opacity: titleIn,
          transform: `translateY(${(1 - titleIn) * 30}px)`,
        }}
      >
        {title}
      </div>

      <div style={{position: 'relative', marginTop: 150, width: LABEL_W + AREA_W, height: chartH}}>
        {/* reference line (below the labels) */}
        <div
          style={{
            position: 'absolute',
            zIndex: 1,
            left: LABEL_W + refX,
            top: -50,
            height: (chartH + 50) * lineIn,
            borderLeft: `5px dashed ${colors.accent}`,
            filter: `drop-shadow(0 0 10px ${colors.accent}88)`,
          }}
        />
        <div
          style={{
            position: 'absolute',
            zIndex: 3,
            left: LABEL_W + refX,
            top: -112,
            transform: `translateX(-50%) scale(${lineIn})`,
            padding: '8px 22px',
            borderRadius: 30,
            background: colors.accent,
            color: '#1a1200',
            fontSize: 36,
            fontWeight: 800,
            whiteSpace: 'nowrap',
          }}
        >
          {reference.label}
        </div>

        {sorted.map((bar, i) => {
          const s = spring({frame: frame - 14 - i * 9, fps, config: {damping: 15, stiffness: 90}});
          const w = (bar.value / max) * AREA_W * s;
          const above = bar.value > reference.value;
          const color = above ? colors.danger : colors.calm;
          return (
            <div key={bar.label} style={{position: 'absolute', top: i * ROW, height: BAR_H, width: '100%'}}>
              <div
                style={{
                  position: 'absolute',
                  left: 0,
                  width: LABEL_W - 28,
                  height: '100%',
                  textAlign: 'right',
                  opacity: Math.min(1, s * 1.5),
                }}
              >
                <div style={{fontSize: 44, fontWeight: 700, lineHeight: bar.note ? '44px' : `${BAR_H}px`, paddingTop: bar.note ? 2 : 0, color: colors.text}}>
                  {bar.label}
                </div>
                {bar.note && (
                  <div style={{fontSize: 26, fontWeight: 500, color: colors.muted, lineHeight: '24px'}}>
                    {bar.note}
                  </div>
                )}
              </div>
              <div
                style={{
                  position: 'absolute',
                  left: LABEL_W,
                  height: '100%',
                  width: w,
                  borderRadius: 14,
                  background: `linear-gradient(90deg, ${color}aa, ${color})`,
                  boxShadow: above ? `0 0 36px ${color}77` : 'none',
                }}
              />
              <div
                style={{
                  position: 'absolute',
                  zIndex: 2,
                  left: LABEL_W + w + 14,
                  top: 6,
                  padding: '0 14px',
                  height: BAR_H - 12,
                  borderRadius: 12,
                  background: 'rgba(10,15,31,0.82)',
                  fontSize: 46,
                  fontWeight: 800,
                  lineHeight: `${BAR_H - 12}px`,
                  color: above ? colors.danger : colors.text,
                  opacity: Math.min(1, s * 1.5),
                  fontVariantNumeric: 'tabular-nums',
                  whiteSpace: 'nowrap',
                }}
              >
                +{fmt(bar.value * s, 1)}
                {unit}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{marginTop: 40, fontSize: 34, fontWeight: 500, color: colors.muted, opacity: lineIn}}>
        {legend}
      </div>
    </AbsoluteFill>
  );
};
