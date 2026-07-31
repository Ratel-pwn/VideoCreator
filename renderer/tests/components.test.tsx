import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it} from 'vitest';
import {StillScene} from '../src/components/StillScene';
import {EditorialFrame} from '../src/components/EditorialFrame';
import {EntityCardScene} from '../src/components/EntityCardScene';
import {
  getSingleLineFontSize,
  normalizeCaptionText,
  SubtitleTrack,
} from '../src/components/SubtitleTrack';
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
  it('renders configured metadata around a clipped editorial viewport', () => {
    const markup = renderToStaticMarkup(
      <EditorialFrame
        frame={{
          preset: 'editorial-wide',
          videoTitle: '资本主义的潘多拉魔盒是如何开启的？',
          publicationDate: '2026.07.21',
          creatorHandle: '@通职者Ratel',
        }}
      >
        <div>timeline</div>
      </EditorialFrame>,
    );

    expect(markup).toContain('资本主义的潘多拉魔盒是如何开启的？');
    expect(markup).toContain('2026.07.21');
    expect(markup).toContain('@通职者Ratel');
    expect(markup).toContain('width:1700px');
    expect(markup).toContain('height:852px');
    expect(markup).toContain('border-radius:24px');
    expect(markup).toContain('overflow:hidden');
  });

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

  it('renders embedded subtitle line breaks as one line', () => {
    const captions = [
      {
        text: '钱原本只是交换工具\n后来却像会自己繁殖的东西。',
        startMs: 0,
        endMs: 1000,
        timestampMs: 0,
        confidence: 1,
      },
    ];
    const markup = renderToStaticMarkup(
      <SubtitleTrack captions={captions} frame={0} fps={25} />,
    );

    expect(markup).toContain('钱原本只是交换工具 后来却像会自己繁殖的东西');
    expect(markup).not.toContain('\n');
    expect(markup).toContain('white-space:nowrap');
  });

  it('shrinks long captions instead of allowing them to wrap', () => {
    const text = '钱原本只是交换工具，后来却越来越像一种会自己繁殖的东西。';

    expect(normalizeCaptionText(`  ${text}\r\n  `)).toBe(text.slice(0, -1));
    expect(getSingleLineFontSize(text)).toBeLessThan(58);
  });

  it('removes trailing punctuation while preserving punctuation within a caption', () => {
    expect(normalizeCaptionText('价格，信号！？。”  \n')).toBe('价格，信号');
    expect(normalizeCaptionText('Market, signal?!')).toBe('Market, signal');
  });

  it('positions captions relative to the editorial media viewport', () => {
    const captions = [
      {text: 'Caption.', startMs: 0, endMs: 1000, timestampMs: 0, confidence: 1},
    ];
    const markup = renderToStaticMarkup(
      <SubtitleTrack captions={captions} frame={0} fps={25} layout="editorial" />,
    );

    expect(markup).toContain('left:80px');
    expect(markup).toContain('right:80px');
    expect(markup).toContain('bottom:54px');
    expect(markup).toContain('max-width:1500px');
    expect(markup).toContain('white-space:nowrap');
  });

  it('renders a fixed entity image over a blurred background with labels', () => {
    const media = {assetType: 'image' as const, assetPath: 'assets/bg.jpg', fitMode: 'cover' as const, trimBeforeFrames: 0, mediaDurationInFrames: 0, shortVideoPolicy: 'reject' as const};
    const markup = renderToStaticMarkup(<EntityCardScene scene={{id: 's', fromFrame: 0, durationInFrames: 25, presentationMode: 'entity_card', backgroundAsset: media, displayAsset: {...media, assetPath: 'assets/book.jpg', fitMode: 'contain'}, entity: {primaryLabel: 'Book', secondaryLabel: 'Title'}}} />);
    expect(markup).toContain('blur(22px)');
    expect(markup).toContain('Book');
    expect(markup).toContain('Title');
    expect(markup).toContain('assets/book.jpg');
  });

  it('keeps entity labels above the display asset', () => {
    const media = {assetType: 'image' as const, assetPath: 'assets/bg.jpg', fitMode: 'cover' as const, trimBeforeFrames: 0, mediaDurationInFrames: 0, shortVideoPolicy: 'reject' as const};
    const markup = renderToStaticMarkup(<EntityCardScene scene={{id: 's', fromFrame: 0, durationInFrames: 25, presentationMode: 'entity_card', backgroundAsset: media, displayAsset: {...media, assetPath: 'assets/book.jpg', fitMode: 'contain'}, entity: {primaryLabel: 'Dutch East India Company', secondaryLabel: 'VOC'}}} />);

    expect(markup).toContain('data-layer="entity-label"');
    expect(markup).toMatch(/data-layer="entity-label"[^>]*z-index:2/);
    expect(markup).toMatch(/data-layer="entity-display"[^>]*z-index:1/);
  });
});
