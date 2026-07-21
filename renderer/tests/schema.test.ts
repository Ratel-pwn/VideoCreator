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

const frame = {
  preset: 'editorial-wide' as const,
  videoTitle: '资本主义的潘多拉魔盒是如何开启的？',
  publicationDate: '2026.07.21',
  creatorHandle: '@通职者Ratel',
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

  it('accepts a complete editorial frame contract', () => {
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
      frame,
    });

    expect(value.frame).toEqual(frame);
  });

  it('rejects incomplete editorial frame metadata', () => {
    expect(() =>
      renderInputSchema.parse({
        videoId: 'fixture',
        width: 1920,
        height: 1080,
        fps: 25,
        durationInFrames: 25,
        audioPath: 'audio/voice.cleaned.mp3',
        subtitlePath: 'audio/voice.cleaned.srt',
        backgroundColor: '#080b0f',
        scenes: [scene],
        frame: {
          preset: 'editorial-wide',
          videoTitle: 'Title',
          creatorHandle: '@Ratel',
        },
      }),
    ).toThrow();
  });

  it('rejects a gap or overlap in the hard-cut timeline', () => {
    expect(() =>
      assertContinuousTimeline(
        [scene, {...scene, id: 'scene-002', fromFrame: 24}],
        50,
      ),
    ).toThrow('expected frame 25, got 24');
  });

  it('accepts v2 entity and explainer scenes', () => {
    const media = {assetType: 'image', assetPath: 'assets/a.jpg', fitMode: 'cover', trimBeforeFrames: 0, mediaDurationInFrames: 0, shortVideoPolicy: 'reject'};
    const value = renderInputSchema.parse({videoId: 'v2', width: 1920, height: 1080, fps: 25, durationInFrames: 50, audioPath: 'audio/a.mp3', subtitlePath: 'audio/a.srt', backgroundColor: '#000', scenes: [
      {id: 's1', fromFrame: 0, durationInFrames: 25, presentationMode: 'entity_card', backgroundAsset: media, displayAsset: {...media, fitMode: 'contain'}, entity: {primaryLabel: 'Book', secondaryLabel: 'Title'}},
      {id: 's2', fromFrame: 25, durationInFrames: 25, presentationMode: 'explainer', backgroundAsset: media, explainer: {kind: 'list', items: ['A', 'B']}},
    ]});
    expect(value.scenes).toHaveLength(2);
  });
});
