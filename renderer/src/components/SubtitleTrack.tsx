import type {z} from 'zod';
import type {captionSchema} from '../schema';

type Caption = z.infer<typeof captionSchema>;

export const SubtitleTrack = ({
  captions,
  frame,
  fps,
}: {
  captions: Caption[];
  frame: number;
  fps: number;
}) => {
  const currentMs = (frame * 1000) / fps;
  const caption = captions.find(
    (candidate) =>
      currentMs >= candidate.startMs && currentMs < candidate.endMs,
  );
  if (!caption) return null;

  return (
    <div
      style={{
        position: 'absolute',
        left: 160,
        right: 160,
        bottom: 150,
        display: 'flex',
        justifyContent: 'center',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          maxWidth: 1600,
          color: 'white',
          fontFamily: 'Noto Sans SC, Microsoft YaHei, sans-serif',
          fontSize: 58,
          fontWeight: 600,
          lineHeight: 1.28,
          textAlign: 'center',
          textShadow:
            '-2px -2px 0 #111, 2px 2px 0 #111, 0 4px 16px rgba(0,0,0,.75)',
          whiteSpace: 'pre-wrap',
        }}
      >
        {caption.text}
      </div>
    </div>
  );
};
