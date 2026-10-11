import React from 'react';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {colors, fmt, fontFamily} from '../theme';

export type BigNumberProps = {
  prefix?: string;
  value: number;
  suffix?: string;
  decimals?: number;
  label: string;
  color?: string;
  // Optional supporting graphic
  meter?: {max: number; unit: string; caption: string};
  car?: boolean;
};

const Car: React.FC<{color: string}> = ({color}) => (
  <svg width="260" height="120" viewBox="0 0 260 120">
    <path
      d="M20 82 L36 50 Q42 38 56 36 L108 34 Q124 16 150 16 L190 18 Q206 22 216 44 L236 52 Q248 56 248 70 L248 82 Z"
      fill={color}
      opacity="0.95"
    />
    <path d="M118 36 Q130 24 148 24 L186 26 Q196 30 202 42 L120 42 Z" fill="#0d1530" opacity="0.85" />
    <circle cx="74" cy="86" r="22" fill="#0a0f1f" stroke={colors.text} strokeWidth="6" />
    <circle cx="198" cy="86" r="22" fill="#0a0f1f" stroke={colors.text} strokeWidth="6" />
  </svg>
);

// Counts up to a value with an optional meter or icon, then slides the label in.
export const BigNumber: React.FC<BigNumberProps> = ({
  prefix = '',
  value,
  suffix = '',
  decimals = 0,
  label,
  color = colors.accent,
  meter,
  car,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const progress = interpolate(frame, [8, 52], [0, 1], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const pop = spring({frame: frame - 2, fps, config: {damping: 12, stiffness: 110}});
  const labelIn = spring({frame: frame - 28, fps, config: {damping: 16, stiffness: 120}});
  const iconIn = spring({frame: frame - 6, fps, config: {damping: 14, stiffness: 90}});

  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', fontFamily}}>
      <div style={{textAlign: 'center', paddingBottom: 110}}>
        {car && (
          <div
            style={{
              height: 130,
              transform: `translateX(${(1 - iconIn) * -500}px)`,
              opacity: iconIn,
              marginBottom: 6,
            }}
          >
            <Car color={color} />
          </div>
        )}
        <div
          style={{
            fontSize: 330,
            fontWeight: 900,
            lineHeight: 1,
            letterSpacing: -10,
            color,
            transform: `scale(${0.8 + 0.2 * pop})`,
            textShadow: `0 0 90px ${color}66`,
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {prefix}
          {fmt(value * progress, decimals)}
          <span style={{fontSize: 150, letterSpacing: -2}}>{suffix}</span>
        </div>

        {meter && (
          <div style={{width: 1000, margin: '34px auto 0'}}>
            <div style={{position: 'relative', height: 34, borderRadius: 17, background: '#1a2447'}}>
              <div
                style={{
                  height: '100%',
                  borderRadius: 17,
                  width: `${(value / meter.max) * 100 * progress}%`,
                  background: `linear-gradient(90deg, ${colors.danger}, ${color})`,
                  boxShadow: `0 0 30px ${colors.danger}88`,
                }}
              />
            </div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                marginTop: 12,
                fontSize: 30,
                fontWeight: 700,
                color: colors.muted,
              }}
            >
              <span>0{meter.unit}</span>
              <span style={{opacity: labelIn}}>{meter.caption}</span>
              <span>
                {meter.max}
                {meter.unit}
              </span>
            </div>
          </div>
        )}

        <div
          style={{
            marginTop: meter ? 26 : 40,
            fontSize: 70,
            fontWeight: 700,
            color: colors.text,
            opacity: labelIn,
            transform: `translateY(${(1 - labelIn) * 40}px)`,
          }}
        >
          {label}
        </div>
      </div>
    </AbsoluteFill>
  );
};
