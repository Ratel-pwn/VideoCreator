import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it} from 'vitest';
import {StillScene} from '../src/components/StillScene';
import {SubtitleTrack} from '../src/components/SubtitleTrack';
import {VideoScene} from '../src/components/VideoScene';
import type {RenderScene} from '../src/schema';

const baseScene: RenderScene = {
  id: 'scene-001',
  fromFrame: 0,
  durationInFrames: 50,
  assetType: 'image',
  assetPath: 'assets/scene-001.jpg',
  fitMode: 'cover',
  trimBeforeFrames: 0,
  mediaDurationInFrames: 0,
  shortVideoPolicy: 'reject',
  motionPreset: 'push-left',
};

describe('render components', () => {
  it('renders still images with cover fit and deterministic motion', () => {
    const element = StillScene({scene: baseScene, frame: 25});
    const image = element.props.children;

    expect(image.props.style.objectFit).toBe('cover');
    expect(image.props.style.transform).toContain('scale(');
  });

  it('renders sourced video without its original audio', () => {
    const element = VideoScene({
      scene: {
        ...baseScene,
        assetType: 'video',
        assetPath: 'assets/scene-001.mp4',
        mediaDurationInFrames: 50,
      },
    });

    expect(element.props.muted).toBe(true);
  });

  it('renders only the caption active at the supplied frame', () => {
    const captions = [
      {text: 'Visible', startMs: 0, endMs: 1000, timestampMs: 0, confidence: 1},
      {text: 'Hidden', startMs: 1000, endMs: 2000, timestampMs: 1000, confidence: 1},
    ];
    const markup = renderToStaticMarkup(
      <SubtitleTrack captions={captions} frame={12} fps={25} />,
    );

    expect(markup).toContain('Visible');
    expect(markup).not.toContain('Hidden');
  });
});
