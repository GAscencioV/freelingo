---
description: "Current-state specification for text-to-speech and speech-to-text providers, backend gateways, plan-derived recognition language, persistent audio, and reusable frontend audio components."
applyTo: "backend/app/services/{tts_service,stt_service,speech_manager,speech_base,chat_markdown}.py, backend/app/routers/{tts,stt}.py, backend/app/schemas/tts_stt.py, backend/app/core/config.py, backend/app/main.py, frontend/src/components/ui/{AudioPlayer,VoiceRecorder,exercise-audio-player}.tsx, frontend/src/app/api/{tts,stt}/**, docker-compose*.yml, .env.example"
---

# Speech Services

## Purpose

The backend is the only gateway to speech providers. Browser code never calls Kokoro, Whisper, OpenAI,
or Gemini speech APIs directly and never receives provider credentials.

Text-to-speech (TTS) and speech-to-text (STT) are configured independently. Their backend service
objects are registered during FastAPI startup inside `app.state.speech_manager` (a `SpeechServiceManager`
instance), with default active references stored in `app.state.tts_service` and `app.state.stt_service`.

## Provider configuration

TTS configuration:

- `TTS_PROVIDER=local` selects Kokoro; `openai` selects OpenAI TTS; `gemini` selects Gemini TTS.
- `TTS_BASE_URL` defaults to `http://kokoro:8880`.
- `TTS_VOICE` defaults to `af_heart` for Kokoro.
- `OPENAI_TTS_BASE_URL` allows overriding the base URL for OpenAI TTS (falls back to `OPENAI_BASE_URL` or default `https://api.openai.com/v1`).
- `OPENAI_TTS_MODEL` defaults to `tts-1` in backend `Settings`. Both Compose files instead supply
  `gpt-4o-mini-tts` when the operator omits the variable; this model is used when `TTS_PROVIDER=openai`.
- `OPENAI_TTS_VOICE` defaults to `nova`.
- `OPENAI_TTS_SPEED` defaults to `1.0`.
- `GEMINI_API_KEY` configures the Google Gemini API key.
- `GEMINI_BASE_URL` defaults to `https://generativelanguage.googleapis.com`.
- `GEMINI_TTS_MODEL` defaults to `gemini-3.8-flash-tts`.
- `GEMINI_TTS_VOICE` defaults to `Kore`.

STT configuration:

- `STT_PROVIDER=local` selects the local Whisper service; `openai` selects OpenAI transcription; `gemini` selects Gemini transcription.
- `STT_BASE_URL` defaults to `http://whisper:9000`.
- `OPENAI_STT_BASE_URL` allows overriding the base URL for OpenAI STT (falls back to `OPENAI_BASE_URL`).
- `OPENAI_STT_MODEL` defaults to `whisper-1`.
- `STT_MODEL` and `STT_ENGINE` configure the local Whisper container as `ASR_MODEL` and
  `ASR_ENGINE`; they are not backend `Settings` fields.
- `GEMINI_STT_MODEL` defaults to `gemini-3.5-transcribe`.

`OPENAI_API_KEY` is required at startup when either selected provider is OpenAI. `GEMINI_API_KEY` is
required at startup when either selected provider is Gemini. Provider values other than `openai` or
`gemini` select the local adapter.

The public configuration exposes the selected TTS provider, default OpenAI TTS voice, and default
Gemini TTS voice. It does not expose provider credentials or the selected STT provider.

Dynamic resolution:
`SpeechServiceManager` allows dynamic per-request provider selection via the optional `X-Speech-Provider`
HTTP header on `/api/tts` and `/api/stt`. When omitted, the configured default `TTS_PROVIDER` or
`STT_PROVIDER` is resolved.

## TTS adapters

### Kokoro

`KokoroTTSService`:

- checks health with `GET {base_url}/v1/models` and a five-second timeout;
- synthesizes with `POST {base_url}/v1/audio/speech` and a 30-second timeout;
- sends model `kokoro`, text, voice, and MP3 response format;
- uses the requested voice or configured `TTS_VOICE`;
- raises on non-success HTTP responses;
- returns response bytes with `media_type="audio/mpeg"`;
- accepts a language argument but does not use it.

### OpenAI

`OpenAITTSService`:

- checks health by listing models;
- sends configured model, requested or default voice, text, MP3 format, and speed to the OpenAI API
  or configured `base_url`;
- trims input and returns empty bytes for empty trimmed text;
- rejects an empty provider audio response;
- records request metadata and latency without logging credentials;
- sends pronunciation `instructions` only for `gpt-4o-mini-tts` and its snapshot identifiers;
- omits `instructions` for `tts-1`, `tts-1-hd`, and unrecognized models, preserving compatibility;
- returns response bytes with `media_type="audio/mpeg"`.

The model remains selected by `OPENAI_TTS_MODEL`; using the `gpt-4o-mini-tts` alias does not pin a
snapshot. `prompts/speech.py` owns instructions shared by all OpenAI synthesis callers. They request
faithful reading, native pronunciation and natural prosody for each language in the text, including
language switches. Text is spoken content, not instructions to execute. Generic calls without a
language ask the model to infer it from the text; isolated ambiguous words cannot reliably identify
a language or regional variety.

Known language arguments add explicit regional guidance for supported learned languages and UI
locales: `es`/`es-ES` requests Spain pronunciation, `pt`/`pt-PT` requests Portugal pronunciation,
`en`/`en-GB` requests British pronunciation, and `en-US` requests American pronunciation. Without a
language argument, instructions do not impose a regional accent. Conversation, Listening, and
Phrasebook supply their full target-language code; the tour supplies its UI locale. Legacy models,
Kokoro, and Gemini ignore this language argument. Model instructions do not alter the input text or
select a different voice.

### Gemini

`GeminiTTSService`:

- checks health with `GET {base_url}/v1beta/models/{model}` and a five-second timeout;
- synthesizes with `POST {base_url}/v1beta/models/{model}:generateContent` with `x-goog-api-key` header
  and a 30-second timeout;
- requests `responseModalities: ["AUDIO"]` and `voiceConfig` with prebuilt system voice name;
- decodes base64 inline audio data from candidate parts;
- returns response bytes with `media_type="audio/wav"`;
- accepts supported voices: `Kore`, `Puck`, `Charon`, `Fenrir`, `Aoede`.

Neither current TTS adapter chooses a model or voice automatically from the target language.

## STT adapters

All STT adapters require an explicit keyword-only ISO 639-1 language on every transcription call.
There is no implicit English fallback.

`WhisperSTTService`:

- checks health with `GET {base_url}/` and a five-second timeout;
- sends `POST {base_url}/asr` with `output=json`, explicit `language`, and `task=transcribe`;
- uploads the file under multipart field `audio_file`;
- uses a 60-second timeout;
- extracts and trims the JSON `text` field.

`OpenAISTTService`:

- checks health by listing models;
- sends configured model, file bytes, filename, MIME type, explicit language, and a 60-second
  timeout to OpenAI transcription (or custom `base_url`);
- extracts and trims the returned text.

`GeminiSTTService`:

- checks health with `GET {base_url}/v1beta/models/{model}` and a five-second timeout;
- sends `POST {base_url}/v1beta/models/{model}:generateContent` with `x-goog-api-key`, inline base64 audio
  under `inlineData`, and an explicit transcription prompt in the specified ISO 639-1 language;
- uses a 60-second timeout;
- extracts and trims the returned text.

## HTTP API

### `POST /api/tts`

- Requires authentication.
- Rate limit: `20/minute`.
- Accepts JSON text of 1-5000 characters and an optional voice string.
- Accepts an optional `study_plan_id` or `conversation_id`, but not both. IDs must be strict positive
  PostgreSQL-range integers; invalid or conflicting context returns `422`.
- Verifies that a supplied plan/conversation belongs to the authenticated user; missing or foreign
  resources return `404`. A plan supplies its persisted BCP-47 language. A conversation uses its
  owned plan's language when present, otherwise its stored target language. Active-language selection
  is never used to resolve supplied context. Without context, synthesis infers language from the text.
- Ignores the client voice when the configured provider is local, preventing stale voice preferences
  from reaching Kokoro. Validates voice name against `_OPENAI_VOICES` when provider is `openai`, or
  against `_GEMINI_VOICES` when provider is `gemini` (`400` on invalid voice).
- Supports dynamic per-request provider override via `X-Speech-Provider` header.
- With `conversation_id`, converts the supplied Markdown to spoken text through
  `chat_markdown_to_speech()` after ownership and language resolution. Bold/italic markers and list
  numbering/bullets are omitted; words, literal punctuation and paragraph/list boundaries are kept.
  Bare list items receive a sentence boundary, using a full-width stop for Japanese and Chinese.
  Formatting carries no guaranteed vocal emphasis. Content without speakable text returns `422`
  without calling the provider. This also applies to replaying voice-origin history from the chat page.
- Plan-only and context-free requests pass their text directly to the provider. The WebSocket voice
  pipeline does not use this conversion. Stored transcripts retain their original content.
- Returns audio bytes matching the active provider's `media_type` (`audio/mpeg` or `audio/wav`).
- Accepts or creates `X-TTS-Trace-ID` and returns backend synthesis, total latency, and `X-TTS-Provider` headers.
- Returns `503` only when no TTS service object is registered; provider exceptions otherwise
  propagate through normal server error handling.

### `GET /api/tts/preview/{voice}`

- Requires authentication.
- Rate limit: `60/minute`.
- Exists for OpenAI and Gemini TTS; local-provider requests return `404`.
- Accepts OpenAI voices (`alloy`, `ash`, `coral`, `echo`, `fable`, `nova`, `onyx`, `sage`, `shimmer`)
  or Gemini voices (`Kore`, `Puck`, `Charon`, `Fenrir`, `Aoede`).
- Invalid voices return `400`; a missing service object returns `503`.
- Generates the Lingu preview and atomically caches it under `/app/tts_previews/` as `{provider}-{voice}.{ext}`
  or `{provider}-{voice}-{key}.{ext}`.
- Returns `Cache-Control: no-store`; Settings requests previews with `cache: 'no-store'` so browser
  caches cannot mask synthesis changes behind the unchanged public URL. The backend disk cache is
  retained.

### `POST /api/tts/tour/{locale}/{step}`

- Requires authentication; rate limit `20/minute`.
- Accepts one of the fifteen UI locales and `step1` through `step7`; unknown values return `404`.
- `messages/*.json` owns tour copy. The frontend resolves the displayed paragraph through i18n and
  sends it as `text` in the JSON request body. The backend validates text length (1–5000 characters;
  invalid or missing text returns `422`) and synthesizes that supplied text.
- An optional JSON `voice` selects a supported OpenAI or Gemini voice (`400` for an invalid selection).
  Omission uses the configured default. Kokoro always uses its configured default, ignoring client voice preferences.
- `services/tour_audio.py` stores shared audio files at
  `{AUDIO_STORAGE_PATH}/tour/{locale}/{sha256}.{ext}`. The hash includes the exact text, locale,
  effective provider/model/voice/speed, output format, and instructions when supported. Requests never
  overwrite a different version.
- Shared-volume file locks serialize generation across workers; cancellable acquisition and synthesis
  share a 60-second deadline. A second request rechecks the file after obtaining the lock. Writes use
  unique temporary files and atomic replacement; empty output and failures do not publish audio.
- Returns audio bytes matching the active provider's format and `Cache-Control: no-store`.

### `POST /api/stt`

- Requires authentication.
- Rate limit: `20/minute`.
- Accepts multipart `audio` and a required positive PostgreSQL-range `study_plan_id`.
- Accepts optional `X-Speech-Provider` header for dynamic per-request provider selection.
- Verifies the plan belongs to the authenticated user.
- Derives the BCP-47 target language from that persisted plan and converts it to ISO 639-1.
- Preserves the uploaded filename and MIME type, with WebM defaults when absent.
- Reads the upload into memory and rejects payloads larger than 50 MiB with `413`.
- Returns `404` for an absent or foreign plan, `422` for invalid multipart data, and `503` when no
  STT service object exists.
- Returns `{ "text": string }`, including an empty string if the provider produces one.

The endpoint does not currently validate accepted MIME types, extensions, non-empty audio, or audio
integrity.

## Resource-owned recognition

Pronunciation exercises and flashcard speaking mode capture the owning `study_plan_id` when recording
starts. A later component update or active-language switch does not alter the in-flight upload.

Lesson pronunciation evaluation compares the transcription with the persisted target sentence using
the plan's target language, the user's native language, and the lesson level. This evaluates
transcribed text, not acoustic phonemes. If LLM evaluation fails, normalized equality or containment
provides the deterministic fallback.

Flashcard speaking comparison occurs in the frontend after STT. The UI shows the transcription,
expected word and expected-word audio before the learner saves the result. Normalized exact equality
proposes SM-2 quality 5; other results propose quality 2. The learner may retry before confirming or
correct a recognition mismatch by confirming quality 5. Only confirmation sends a review request;
recordings and retries do not award XP or alter scheduling. The latest transcription determines the
proposed quality. Empty normalized transcription keeps the recorder available with a retry message.
This comparison does not evaluate acoustic pronunciation. A failed save retains the result, and
concurrent confirmation is blocked until the request settles.

Voice conversation uses its own capture pipeline and WebSocket contract, described in
`voice-conversation.instructions.md`.

## Frontend components and proxies

`AudioPlayer` requests TTS, creates a Blob URL, and plays it with the browser Audio API. Voice
precedence is explicit prop, stored `tts_voice`, then backend default. It supports loading, playing,
stop, and error states and is used across lessons, flashcards, vocabulary, chat, and phrasebook.

`AudioPlayer` defaults to POST `/api/tts`; a custom `audioUrl` defaults to GET. Setting `audioMethod`
to POST sends the text, resolved voice, and optional context as JSON to that URL. Both GET and POST audio
use the shared authenticated `apiFetch` path.

The dedicated Next.js TTS route forwards authentication, trace context, and `X-Speech-Provider` header,
and dynamically preserves the backend's `Content-Type` header (`audio/mpeg` or `audio/wav`).
The STT route parses and reconstructs multipart data, forwards auth, cookies, and `X-Speech-Provider`,
and propagates request cancellation to the backend.

## Persistent and transient audio

`POST /api/tts`, STT recordings, and voice-conversation audio are transient.

Persistent audio uses include:

- Listening: `{AUDIO_STORAGE_PATH}/listening/{exercise_id}.mp3`.
- Phrasebook: hashed files below `{AUDIO_STORAGE_PATH}/phrasebook/{iso}/`.
- Voice previews: `/app/tts_previews/{provider}-{voice}.{ext}` or `{provider}-{voice}-{sha256}.{ext}`.
- Dashboard tour: `{AUDIO_STORAGE_PATH}/tour/{locale}/{sha256}.{ext}`.

Phrasebook and tour audio cache identities include model, provider, voice, speed, format, and pronunciation
instructions when supported.

## Availability semantics

Startup creates configured adapter objects in `SpeechServiceManager` but does not prove provider health.
Administrative health checks call adapter `health()` methods. Conversation warmup attempts provider work
in parallel within a shared 60-second budget.

## Related specifications

- `voice-conversation.instructions.md` — WebSocket conversation pipeline.
- `listening.instructions.md` — persistent TTS audio for Listening exercises.
- `multi-language.instructions.md` — plan ownership and target-language rules.
- `services.instructions.md` — complete backend service inventory.
- `docker.instructions.md` — deployment topology and provider containers.
