import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {parseSrt} from '@remotion/captions';
import {renderMedia, selectComposition} from '@remotion/renderer';

const args = process.argv.slice(2);
const valueFor = (name) => {
  const index = args.indexOf(name);
  if (index === -1 || !args[index + 1]) {
    throw new Error(`Missing required argument: ${name}`);
  }
  return path.resolve(args[index + 1]);
};
const report = (event, detail = {}) =>
  process.stdout.write(`${JSON.stringify({event, ...detail})}\n`);

const projectRoot = valueFor('--project-root');
const inputPath = valueFor('--input');
const outputPath = valueFor('--output');
const rendererRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const input = JSON.parse(await readFile(inputPath, 'utf8'));
const subtitleText = await readFile(
  path.resolve(projectRoot, input.subtitlePath),
  'utf8',
);
const {captions} = parseSrt({input: subtitleText});
const inputProps = {...input, captions};

report('bundle-start');
const serveUrl = await bundle({
  entryPoint: path.join(rendererRoot, 'src', 'index.ts'),
  publicDir: projectRoot,
  onProgress: (progress) => report('bundle-progress', {progress}),
});
report('bundle-complete');

const composition = await selectComposition({
  serveUrl,
  id: 'NarratedVideo',
  inputProps,
});
report('render-start', {durationInFrames: composition.durationInFrames});
await renderMedia({
  serveUrl,
  composition,
  inputProps,
  codec: 'h264',
  audioCodec: 'aac',
  outputLocation: outputPath,
  overwrite: true,
  enforceAudioTrack: true,
  onProgress: ({progress}) => report('render-progress', {progress}),
});
report('render-complete', {output: outputPath});
