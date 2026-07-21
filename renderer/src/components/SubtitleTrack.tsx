import type {z} from 'zod';
import type {captionSchema} from '../schema';

type Caption = z.infer<typeof captionSchema>;

const MAX_FONT_SIZE = 58;
const MAX_CAPTION_WIDTH = 1600;
const WIDTH_SAFETY_FACTOR = 0.94;

export const normalizeCaptionText = (text: string) =>
  text.replace(/\s+/g, ' ').trim().replace(/\p{P}+$/gu, '').trimEnd();

const estimateTextWidthInEm = (text: string) =>
  Array.from(text).reduce((width, character) => {
    if (/\s/.test(character)) return width + 0.35;
    if (character.codePointAt(0)! > 0xff) return width + 1;
    if (/[A-Z]/.test(character)) return width + 0.7;
    if (/[a-z0-9]/.test(character)) return width + 0.58;
    return width + 0.65;
  }, 0);

export const getSingleLineFontSize = (text: string) => {
  const estimatedWidth = estimateTextWidthInEm(normalizeCaptionText(text));
  if (estimatedWidth === 0) return MAX_FONT_SIZE;

  return Math.min(
    MAX_FONT_SIZE,
    Math.floor(
      ((MAX_CAPTION_WIDTH * WIDTH_SAFETY_FACTOR) / estimatedWidth) * 10,
    ) / 10,
  );
};

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

  const text = normalizeCaptionText(caption.text);

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
          maxWidth: MAX_CAPTION_WIDTH,
          color: 'white',
          fontFamily: 'Noto Sans SC, Microsoft YaHei, sans-serif',
          fontSize: getSingleLineFontSize(text),
          fontWeight: 600,
          lineHeight: 1.28,
          textAlign: 'center',
          textShadow:
            '-2px -2px 0 #111, 2px 2px 0 #111, 0 4px 16px rgba(0,0,0,.75)',
          whiteSpace: 'nowrap',
        }}
      >
        {text}
      </div>
    </div>
  );
};
