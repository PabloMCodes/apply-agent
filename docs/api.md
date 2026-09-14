# Local API

This API is for one private installation. The UI and background workers share its
SQLite data. Keep it bound to loopback or behind a private connection. Interactive
request/response definitions are at `/docs`; the schema is `/openapi.json`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Local web interface |
| GET | `/health` | API/database check |
| GET, PUT | `/profile` | Application facts and confirmed resume text |
| DELETE | `/profile` | Remove profile text/details |
| GET, POST, DELETE | `/resume` | Resume metadata, PDF/TXT upload, removal |
| GET | `/resume/file` | Download current uploaded file |
| GET, PUT | `/settings` | Keyword preferences, polling, private review URL |
| GET, POST | `/settings/sources` | Configured company boards and bookmarks |
| PATCH, DELETE | `/settings/sources/{id}` | Toggle monitoring or remove a source |
| POST | `/settings/sources/{id}/check` | Import a source now, respecting alert preferences |
| GET | `/settings/telegram` | Connection status (no token) |
| POST | `/settings/telegram/connect` | Verify bot token and generate expiring pairing link |
| DELETE | `/settings/telegram` | Disconnect the bot |
| GET, POST | `/jobs` | Search/list or manually create a job |
| GET, DELETE | `/jobs/{id}` | Read or delete a local job |
| PUT | `/jobs/{id}/tracking` | Replace tracking status/notes |
| POST | `/jobs/{id}/prepare` | Approve/queue form discovery and preparation |
| GET | `/applications` | Queue and browser run status |
| GET | `/applications/{id}` | Current form snapshot and revision |
| GET | `/applications/{id}/screenshot` | Latest form screenshot |
| POST | `/applications/{id}/edit` | Queue an answer change for a specific revision |
| POST | `/applications/{id}/options` | Read employer dropdown choices, optionally searching |
| POST | `/applications/{id}/submit` | Explicitly approve submission of a reviewed revision |
| POST | `/applications/{id}/cancel` | Close a prepared browser without submitting |
| POST | `/applications/{id}/retry` | Reprepare an expired/failed draft, never an unknown submission |
| GET | `/sources` | Legacy built-in GitHub source catalog |
| POST, GET | `/ingestions` | Legacy direct GitHub import or successful import history |
| GET | `/ingestions/{id}` | Successful ingestion record |
| POST | `/matches` | Reserved AI contract; currently 501 |

Mutation failures use 404 for missing records, 409 for duplicates/stale revisions
or conflicting actions, 422 for invalid/unsupported input, and 502 for upstream
errors. Browser commands return 202, then complete asynchronously; poll the current
application revision/status. Deletions return 204.

Job listing accepts `q`, `status`, `limit` (1–200), and `offset` and returns
`{items,total,limit,offset}`. Application and ingestion lists accept limit/offset
and return arrays. Sources are a small configuration list.

To edit a field, use its ID from the latest snapshot:

```json
{"revision": 3, "field_id": "0:f6", "value": "My confirmed answer"}
```

Dropdown choice requests accept `{revision, field_id, query}`. Submit requests
accept `{revision}` and must represent a user's explicit final decision. The
browser verifies current fields against the reviewed fingerprint before clicking.

API calls without an Origin header support local CLI clients. Browser requests
from unrelated origins/hosts are rejected. These safeguards are not authentication
and do not make the app suitable for an untrusted public network.

- `POST /applications/prepare-batch` accepts `{"job_ids":[1,2,3,4,5]}` (maximum 500 per request; the UI chunks larger selections).
  Accepts optional `resume_id` for an explicit choice. Returns per-job `application` or `error`; successful jobs remain queued if others fail.
- `GET /saved-answers` lists local remembered answers; `DELETE /saved-answers/{id}` forgets one.
- `POST /applications/{id}/edit` additionally accepts `remember` (default false) and
  `scope` (`company` default, or `all`). Memory is written after the browser retains
  the edit. Reused fields have `saved_answer_id` and block submission until confirmed
  with an edit request at the current revision.


## Expanded profile and preparation

- `PUT /profile` supports `experience`, `accomplishments`, `education`,
  `work_preferences`, `availability`, and `facts`. A fact has `id`, `question`,
  `answer`, `variants`, `context`, `sensitive`, and `share_with_ai`.
- `GET /resumes`; `POST /resumes` multipart `title`, optional `roles`, and `file`.
  `PATCH /resumes/{id}` renames title/roles; `DELETE /resumes/{id}` removes it.
  `GET /resumes/{id}/file` downloads a library file. The legacy `/resume` API remains.
- `GET /applications/{id}/resume/file` downloads the frozen file used by that run.
- `POST /jobs/bulk` accepts `{jobs:[JobInput,...]}` (maximum 500 per request),
  upserts URLs, and preserves existing tracking. It does not queue preparation.
- `PUT /settings` supports `review_slots` (1–20, default 5). Reducing the value does
  not close existing reviews; new preparation waits until there is capacity.
- `POST /applications/{id}/navigate` accepts `{revision,direction:"next"|"back"}`.
  Snapshot `pages` contains recorded earlier fields; `page_number`, `can_next`,
  `can_back`, and `resume` describe the current review.
- `GET/PUT /settings/ai`: `enabled`, `base_url`, `model`, `api_key` (write-only).
  An omitted/null key preserves the current key only if the base URL is unchanged;
  an empty key removes it. AI is disabled by default.
- `POST /applications/{id}/suggest` accepts `{revision,field_id}`. The async worker
  requests a draft without filling it. The field's `review` contains `status`,
  `source`, `pending`, optional `draft`, and AI `evidence`.

Queue entries persist without a live-session cap; only open browser reviews use
slots. Re-selecting an expired/failed/cancelled job requeues it. A submitted or
unknown-outcome application is never automatically requeued.

## SWE profile and resume prefill

`GET /profile/fields` returns the common-question catalog (sections, choices, and
hints). `PUT /profile` accepts `github` and `application_answers`, keyed by catalog
field names. Invalid choice values and unknown keys are rejected. Blank values
mean unanswered. Legacy narrative/fact fields remain API-compatible but are removed
from the UI.

Resume uploads return `profile_suggestions: {values,evidence,message}` in addition
to file metadata. `GET /resumes/{id}/profile-suggestions` extracts from a saved file.
`values.application_answers` contains school/degree/major/graduation/GPA and explicit
current employer/title labels when found. No demographic or eligibility fields are
extracted. The UI fills empty fields and requires Save profile; these endpoints do
not overwrite a saved profile.


## Browser takeover and optional AI field mapping

`POST /applications/{id}/browser` accepts a `BrowserControl` object. Unlike normal
browser commands, these actions use a bounded in-memory queue (no SQLite payload),
and return when completed. This endpoint requires the integrated worker runtime.

- `operation: "start"` pauses automatic handling and returns a viewport.
- `refresh` returns a new viewport/token without input actions.
- `click`, `mark_final`, `upload_resume` accept viewport pixel `x,y` and the latest
  `token`. A changed page or stale token rejects the action.
- `type` accepts `text`; it fills the currently focused editable field. Do not log
  request bodies. It does not interpret text as keyboard shortcuts or commands.
- `key` accepts only Tab/Escape/Backspace/arrow keys. Enter is intentionally absent.
- `scroll` accepts a bounded `delta`; `tab` selects a returned tab index.
- `save_session` explicitly saves encrypted storage state for the application origin.
- `resume` returns control to preparation/review; `ai` requests constrained mapping
  from saved facts on the current application page before resuming review.

Viewport responses contain JPEG `image` as base64, `token`, sanitized `url`, dimensions,
and available `tabs`. No login screenshot is stored on disk. Resuming returns
`{resumed:true,message}`; read the updated application snapshot afterward. A timeout
requires refresh before issuing another action; queued cancelled inputs are discarded.
A started action may have completed, so never blindly retry it.

`GET /browser-sessions` returns only origin and save/expiry timestamps.
`POST /browser-sessions/forget` accepts `{origin:"https://careers.example.com"}`.
It removes the local encrypted state, not live contexts or employer-side sessions.

AI settings additionally accept `browser_assistance` (default false). With `enabled`
and this flag true, preparation may map unfamiliar fields to exact allowed profile
sources once per page. No password, identity, or work-authorization sources are sent.
Provider responses cannot supply executable actions or arbitrary values. Field labels
are rechecked before applying the plan; populated fields are marked for user review.
Final submission still uses the existing revision/fingerprint confirmation endpoint.
