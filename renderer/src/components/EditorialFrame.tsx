import type {ReactNode} from 'react';
import {AbsoluteFill} from 'remotion';
import type {EditorialFrameConfig} from '../schema';

const fontFamily = 'Noto Sans SC, Microsoft YaHei, sans-serif';

export const EditorialFrame = ({
  frame,
  children,
}: {
  frame: EditorialFrameConfig;
  children: ReactNode;
}) => (
  <AbsoluteFill style={{backgroundColor: '#2b2b2a'}}>
    <div
      style={{
        position: 'absolute',
        top: 36,
        bottom: 36,
        left: 0,
        right: 0,
        backgroundColor: '#f7f6f2',
      }}
    />
    <header
      style={{
        position: 'absolute',
        top: 36,
        left: 110,
        right: 110,
        height: 84,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        color: '#292927',
        fontFamily,
      }}
    >
      <div
        style={{
          maxWidth: 1320,
          overflow: 'hidden',
          fontSize: 34,
          fontWeight: 600,
          letterSpacing: 0.5,
          lineHeight: 1,
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {frame.videoTitle}
      </div>
      <div
        style={{
          flexShrink: 0,
          fontSize: 26,
          fontWeight: 500,
          letterSpacing: 4,
          lineHeight: 1,
        }}
      >
        {frame.publicationDate}
      </div>
    </header>
    <div
      style={{
        position: 'absolute',
        left: 110,
        top: 120,
        width: 1700,
        height: 852,
        overflow: 'hidden',
        borderRadius: 24,
        backgroundColor: '#080b0f',
        boxShadow: '0 16px 42px rgba(25, 24, 21, 0.2)',
      }}
    >
      {children}
    </div>
    <footer
      style={{
        position: 'absolute',
        left: 110,
        right: 110,
        top: 972,
        height: 72,
        display: 'flex',
        alignItems: 'center',
        color: '#777570',
        fontFamily,
        fontSize: 24,
        fontWeight: 500,
        letterSpacing: 0.5,
      }}
    >
      {frame.creatorHandle}
    </footer>
  </AbsoluteFill>
);
