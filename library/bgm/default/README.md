# Global BGM Library

Put reusable local BGM tracks in this directory. Each supported audio file must
have a same-stem `.bgm.json` sidecar. Project libraries completely override this
directory, followed by template libraries; tracks from different levels are not
merged. Invalid or undecodable tracks are skipped with a warning.

Required sidecar fields are `schema_version`, `id`, `title`, `subjects`,
`moods`, `energy`, and `instrumental`. Optional fields are `creator`,
`source_url`, `provider`, `license`, `rights_status`, `tempo_bpm`,
`template_tags`, `avoid_for`, `preferred_start_ms`, and `loopable`.
`source_url` must use HTTP(S) and contain no userinfo, query, or fragment.
