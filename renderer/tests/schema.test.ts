import {describe, expect, it} from 'vitest';
import {renderInputSchema} from '../src/schema';
import {assertContinuousTimeline} from '../src/timeline';

const scene = {
  id: 'scene-001',
  fromFrame: 0,
  durationInFrames: 25,
  assetType: 'image' as const,
  assetPath: 'assets/scene-001.jpg',
  fitMode: 'cover' as const,
  trimBeforeFrames: 0,
  mediaDurationInFrames: 0,
  shortVideoPolicy: 'reject' as const,
  motionPreset: 'push-left' as const,
};

describe('render input', () => {
  it('accepts the locked 1080p 25fps contract', () => {
    const value = renderInputSchema.parse({
      videoId: 'fixture',
      width: 1920,
      height: 1080,
      fps: 25,
      durationInFrames: 25,
      audioPath: 'audio/voice.cleaned.mp3',
      subtitlePath: 'audio/voice.cleaned.srt',
      backgroundColor: '#080b0f',
      scenes: [scene],
    });

    expect(value.captions).toEqual([]);
  });

  it('rejects a gap or overlap in the hard-cut timeline', () => {
    expect(() =>
      assertContinuousTimeline(
        [scene, {...scene, id: 'scene-002', fromFrame: 24}],
        50,
      ),
    ).toThrow('expected frame 25, got 24');
  });
});
