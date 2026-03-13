# Workflow Roadmap

## Current status

Already implemented now:

- Topic discussion through a unified CLI workflow
- Draft generation from conversation records
- Voice generation through the Volcengine TTS script
- Subtitle alignment using original text plus Whisper timestamps
- Visual planning from `.srt` into `drafts/visual-plan.json`
- Asset collection and fallback generation from the visual plan

Not implemented yet:

- Final video assembly from prepared assets
- Automatic segment-to-timeline compositing
- Provider-specific video editing pipeline

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

## Asset generation stage

The new asset build stage reads `drafts/visual-plan.json` and resolves one asset per segment.

Resolution order is:

1. Search for reusable online material first
2. If no usable material is found, generate with Jimeng image or video APIs
3. Save every material file into the project `assets/` directory
4. Name files with `timestamp + visual brief`

## Next major addition

The next major stage is no longer planning or raw asset generation. It is final timeline assembly.

That future stage should:

- read the subtitle timing and visual asset manifest
- place every image/video asset onto a timeline
- decide hold durations, pans, zooms, and transitions
- render a final video draft for review

## Pending decisions

These decisions are still intentionally deferred:

- which final editing/rendering stack to use
- whether image-only segments should use motion pan/zoom by default
- whether subtitle-only segments should render as empty hold, typography card, or background texture
- whether search providers should expand beyond Wikimedia and Pexels

## Important note

The project now produces the following middle artifacts before full video assembly:

- `voice.srt`
- `drafts/visual-plan.json`
- `runs/asset-manifest.json`

The final video render stage is still not implemented and should be treated as the next milestone.
