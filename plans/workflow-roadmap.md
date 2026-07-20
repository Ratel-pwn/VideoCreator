# Workflow Roadmap

## Current status

Already implemented now:

- Topic discussion through a unified CLI workflow
- Draft generation from conversation records
- Voice generation through the Volcengine TTS script
- Subtitle alignment using original text plus Whisper timestamps
- Visual planning from `.srt` into `drafts/visual-plan.json`
- AI-curated web asset requests, provenance manifests, and media audit
- Final Remotion timeline assembly with narration and burned-in subtitles
- Verified 1920x1080, 25fps H.264/AAC output

Not implemented yet:

- Vertical-video output
- Richer transitions beyond hard cuts
- Timeline editor UI

## Visual planning stage

The new visual planning stage is implemented with the `segment-visual-planner` skill.

Its job is to read the final `.srt` subtitle file and generate a `drafts/visual-plan.json` file that decides, for each segment, whether the visual should be:

- video footage
- still image
- subtitle only / no external visual

The visual plan also carries:

- search keywords
- generation prompts
- transition suggestions
- visual role such as evidential, illustrative, abstract, or atmospheric

## Asset resolution stage

The new asset build stage reads `drafts/visual-plan.json` and resolves one asset per segment.

The current no-API workflow is:

1. Generate one explicit web-research request per non-text scene
2. Have AI find reusable online material and retain source provenance
3. Save every material file into the project `assets/` directory
4. Audit resolution, media type, review status, and source fields before rendering

Generation adapters remain available for future use but are disabled by default.

## Final assembly stage

Final timeline assembly is implemented with Remotion. It:

- validates the approved asset manifest
- removes confirmed trailing narration silence into derivative audio/SRT files
- converts visual-plan timings into a continuous 25fps hard-cut timeline
- applies restrained directional motion to still images
- renders narration and burned-in subtitles into a verified MP4

## Pending decisions

These decisions remain intentionally deferred:

- vertical output layout and subtitle sizing
- transition styles beyond hard cuts
- richer subtitle-only scene art direction
- whether search providers should expand beyond Wikimedia and Pexels

## Important note

The final stage produces:

- `audio/voice.cleaned.mp3` and `audio/voice.cleaned.srt` when trimming is required
- `runs/<run-id>/render-input.json`
- `runs/<run-id>/final.mp4`
- `runs/<run-id>/render-report.json`
