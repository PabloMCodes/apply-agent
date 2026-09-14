# Apply Agent

A self-hosted, single-user job application workspace. Set up your resume and
preferences in a local web interface, follow company job boards across occupations,
receive Telegram alerts, and prepare supported applications for your review.

No product account, browser extension, frontend build, or paid AI API is required.
The interface is plain HTML/CSS/JavaScript served by Python/FastAPI; personal data
lives in SQLite and files inside your installation. Licensed under MIT.

## Start with Docker

Install [Docker with Compose](https://docs.docker.com/engine/install/), then run
from this project directory:

```sh
docker compose up -d --build
```

Open **http://localhost:8000**. One service starts the web interface, source
monitor, Telegram listener, and browser worker. No `.env` file is required.
The initial build downloads Chromium and can take several minutes.

## Local Python development

Python 3.10+ on macOS/Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

On Linux, install the browser's OS dependencies with
`python -m playwright install --with-deps chromium`. Use Docker on Windows.
If you already have Google Chrome installed, `BROWSER_CHANNEL=chrome python main.py`
is also supported with a fresh isolated browser profile.

### Local browser windows

`python main.py` now defaults to **native** mode on a Mac or Linux desktop.
The worker opens your selected applications as tabs in one visible Chromium
window and fills them one at a time. Open an application to focus its exact tab;
fields, cookies, uploads, and the current page stay intact. Type and scroll in
Chromium directly, without the screenshot/input relay. Let preparation finish or
pause before editing the window so your input does not race the worker.

Finish reviewing before submitting in the employer page. Then choose **I submitted
this application** in Apply Agent to update tracking and close the window. This
records your report; it does not click Submit or verify employer acceptance.
Only that application’s tab closes; the others remain open.
**Review saved application details** captures your current answers without refilling or advancing.
Keep the window open until you record completion. Closing it alone never means submitted.

For remote servers or phone access, use `BROWSER_MODE=stream python main.py`.
Docker and `uvicorn main:app` default to streamed mode; a native window opens on
the host computer, not the phone. `BROWSER_MODE=native` explicitly enables desktop
mode when launching through another entry point. Switching modes requires a restart
and fresh preparation: an existing headless session cannot become a desktop window.

### Automatic answer memory

After preparation finishes or pauses, native tabs remember manual text, select,
recognized single-choice custom dropdown, and labelled radio-group answers. Input
is saved after a short pause, a field change, or leaving the field; listeners are
installed on subsequent pages too. Worker-generated fills are excluded. Reused
answers still need review. Unsupported controls or ambiguous labels are not learned.

Ordinary answers are reusable across companies; questions naming the employer or
asking why you want this company/role stay company-scoped. Exact question matching
and existing conflict checks apply. Clearing an answer removes that saved mapping.
Manage answers in **Profile & resume**, or turn learning off in **Preferences**.
The application page shows how many changes were saved. Native mode has no preview.

Passwords, verification codes, signature fields, file inputs, and consent checkboxes
are excluded. Saved responses and a per-application change history stay in your local
SQLite database. Forgetting a reusable answer prevents reuse but does not erase the
application's change history. This is not an AI inference feature or a recording of
all browser activity. Keep the worker running while editing application tabs.

## Set up your workspace

1. **Profile & resume:** upload titled PDF or UTF-8 text resumes (up to 5 MB each). Review the
   extracted text and explicitly save your first/last name, email, phone, and links.
   Resume uploads prefill empty contact, link, skills, and education fields locally.
   Review the suggestions and Save profile. Scanned PDFs without text cannot prefill.
2. **Preferences:** choose title/location keywords and exclusions, and enable
   scheduled monitoring. Empty keyword lists include all roles or locations.
3. **Job sources:** add a Greenhouse company board, hosted job URL, or a Greenhouse-backed CareerPuck job link.
   Use **Check now** for the initial import. The original software-specific GitHub
   lists remain available as optional sources. Other websites are saved as
   clearly labelled bookmarks, not silently treated as supported scrapers.
4. **Telegram:** create a bot through the official [@BotFather](https://t.me/BotFather),
   paste its token in the interface, and follow the expiring pairing link. Press
   Start in your own private chat. A phone number alone does not link Telegram.
5. **Discover jobs:** choose **Prepare application**, or approve a Telegram alert.
6. **Applications:** review the live form screenshot and every answer. Save any
   changes; use **Find choices** for supported Greenhouse dropdowns. Only after
   your review can you explicitly confirm **Submit**.

No real application is submitted during setup or automated tests.

## What works today

| Capability | Current behavior |
|---|---|
| Local setup UI | Responsive profile, resume, preferences, sources, Telegram, jobs, review |
| Job discovery | Public Greenhouse boards, Greenhouse-backed CareerPuck jobs, and SpeedyApply lists |
| Other URLs | Saved bookmarks or manual job records; preparation attempts form detection |
| Alerts | Quiet initial baseline; later unseen URLs matching preferences trigger alerts |
| Filtering | Literal, case-insensitive role/location/exclusion keywords; no AI scores |
| Telegram | Single-owner pairing, approval/skip buttons, `/status`, `/queue`, `/help` |
| Autofill | Greenhouse (including embedded forms) and best-effort standard HTML forms; no guessed screening answers |
| Review | Screenshot, editable standard fields and supported single-select dropdowns |
| Submission | Separate explicit approval of the current form revision; never retried automatically |
| Storage | SQLite plus resume/screenshot files, persisted in one Docker volume |

Preparation now accepts public HTTPS job URLs instead of requiring a Greenhouse
hostname. It detects embedded Greenhouse forms (including CareerPuck's Lyft pages)
and opens the underlying hosted form. For other sites, it tries a single standard
HTML application form and fills only known fields inside that form. It can follow
one explicit Apply link. Verified application forms can advance through unique Next,
Continue, and Review buttons (up to 20 steps per preparation), retaining earlier
answers. Missing required inputs or ambiguous navigation pause the flow. It stops
at a recognized final Submit button, with or without an employer review page.
This is best-effort coverage, not a guarantee that every application website works.

Custom controls, multi-selects, login challenges, or CAPTCHAs may require your help.
Use **Open live browser** to operate supported controls in the same server session.
Unknown buttons and arbitrary canvas interactions remain unsupported; this is not
a full remote desktop. Unsupported required controls block submission.
Opening the employer link on your phone creates a separate session and does **not**
transfer the filled form; use Apply Agent's review page for supported applications.

Streamed browser sessions expire after two hours, with five live review slots by default (configurable from 1–20). Native tabs remain until closed or the worker stops; selected jobs are opened in sequence without the streamed slot limit.
Profile changes do not silently rewrite an already prepared application. Its resume
file is frozen when preparation starts. A restart expires live drafts; prepare them
again. A crash or ambiguous response during submission becomes `submission_unknown`
and must be checked with the employer or email, rather than automatically retried.

## Data, networking, and costs

The default server binds to loopback. This is personal software with no public
login or multi-user isolation: use a private connection for phone/server access.
See [self-hosting and phone review](docs/self-hosting.md) for setup and migration.

Resume data is stored locally until you explicitly prepare an employer application;
at that point selected facts and the chosen resume are sent to that employer's
form. Telegram receives job alerts and status updates. AI is off by default;
clicking Suggest answer sends the profile sections and explicitly permitted facts
described in Profile to your configured provider. Credentials stay on the server.

Telegram messaging has no normal personal-use API fee; running an always-on server
is separate. A local computer must stay awake for background work. These files do
not provision or promise free hosting.

Personal files, SQLite databases, browser artifacts, and secrets are ignored by
Git and excluded from Docker build context. Do not commit a populated data folder.
Back up the Docker volume with the app stopped for a consistent copy.

## Development and tests

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
RUN_BROWSER_TESTS=1 python -m pytest -q
```

The first command runs offline API, persistence, parsing, pairing, and approval
checks. Opt-in browser tests require Chromium, use synthetic employer forms and a
temporary local server, and never submit real applications. To use installed Chrome:

```sh
RUN_BROWSER_TESTS=1 BROWSER_CHANNEL=chrome python -m pytest -q
```

The generated API schema is at `/openapi.json`; interactive docs are at `/docs`.
See [the API guide](docs/api.md). Keep one application process/replica per database
and bot. Do not run the older standalone Telegram worker alongside `main.py`.

```text
main.py                 Web app and background-service entry point
src/web/                Bundled HTML, CSS, and JavaScript
src/api/                Existing jobs/profile API
src/setup/              Settings, resume extraction, and setup routes
src/jobs/               GitHub and Greenhouse discovery/parsing
src/db/                 SQLite persistence
src/telegram/           Bot client, messages, approvals, and legacy CLI
src/applications/       Browser adapter, review state, and commands
src/runtime.py          Single-installation background services
src/ai/                Optional provider adapter for source-linked answer drafts
src/matching/scorer.py  Future AI job ranking placeholder
tests/                  Offline and opt-in browser tests
```

### Saved answers and batches

In Applications, expand **Add and select applications** to paste hundreds of job
records, select across pages, or select all matching jobs. Click **Prepare selected**.
There is no UI selection cap; requests are sent in chunks of 500. The worker prepares
one at a time. Native mode keeps them as tabs in one window; streamed mode retains a configurable number of isolated review sessions. Additional
queued jobs wait for a slot. Keep Apply Agent running; employer sessions can expire
sooner than the streamed mode’s two-hour review window. Applications updates status automatically. Closing or submitting a review frees
a slot for the next queued job.
Unsupported forms or unanswered questions still need your attention.

While reviewing, **Update answer** changes only this application. Check **Remember
this answer** to save it locally, choosing this company (default) or all companies.
Only exact normalized question and control-type matches are reused; ambiguous fields,
consent checkboxes and radio buttons are excluded. Dropdown choices must still match.
Use company scope for employer-specific answers. Every reused answer is marked and
must be confirmed before submission, including sensitive or time-dependent answers.
Manage or forget saved answers in **Profile & resume → Saved answers**. Forgetting an answer
prevents future reuse; it does not erase a value already entered into a live form.
Each application still requires its own final review and explicit submission.


### Profile facts, question matching, and optional AI

The profile now uses common SWE application questions: school/degree/major,
graduation year/GPA, current employer/title, start date, salary, relocation/hybrid,
country-specific authorization and sponsorship, citizenship, and optional gender,
Hispanic/Latino ethnicity, separate or combined race/ethnicity, protected veteran,
and disability status. All default to unanswered. See [research notes](docs/swe-profile-research.md).
Explicit saved voluntary answers now fill supported dropdowns when there is one
matching employer choice, including narrow wording equivalents such as Man/Male.
Every reused choice still requires confirmation before submission. Pronouns are
not derived from gender; combined race/ethnicity requires its own saved answer.
Unknown, ambiguous, and conflicting choices stay unanswered.
The former narrative boxes and custom fact editor have been removed from the UI;
previously saved data remains compatible with the API.

Uploading a titled resume prefills empty profile fields and shows extraction evidence.
It does not overwrite existing answers or save unreviewed changes. Click **Save profile**
after review. Existing files have **Fill profile from this resume**. Extraction is
local and works on PDF text or UTF-8 TXT. Unsupported layouts remain for manual entry;
identity, disability, veteran status, citizenship, authorization, and sponsorship are
never inferred from resume text. Only explicit current-employer labels are extracted;
job history dates are not used to guess your current job or years of experience.

Review badges distinguish **Previously confirmed**, **New wording—check mapping**,
**AI draft—review required**, and **Needs your answer**. Each suggestion identifies
its source. Saved-answer and new fact mappings must be confirmed; prior pages with
unconfirmed suggestions block final submission. Previous/Next controls let you
return to employer fields when that site exposes supported navigation. Recorded
answers remain visible even when the employer has no review screen. Unsupported
navigation, login, CAPTCHA, and custom widgets still require site-specific work.

Each resume needs a descriptive title. Automatic selection uses title/role keyword
overlap, not AI; ties or zero matches among multiple resumes pause preparation.
You can explicitly choose a resume for a selection. Review displays the chosen
title and reason, and provides a download of the frozen file actually used.

Optional AI uses an OpenAI-compatible Chat Completions endpoint, including local
models. Configure the API base URL, model name, and optional key in Profile.
The model adapter does not log into ChatGPT Plus or use a consumer subscription token.
Provider API access must be configured separately. See the official
[API reference](https://developers.openai.com/api/reference/resources/chat).
Other providers with different protocols need another adapter.

AI answer drafting runs when you click **Suggest answer with AI**. Optional browser
field mapping also runs during preparation if separately enabled in Profile. It receives no browser
credentials or tools and cannot navigate or submit. Responses need known source
references and valid employer choices. Missing evidence, malformed responses,
and provider failures leave the form unchanged. Source references do not prove a
draft is accurate: inspect the shown evidence, copy/edit the draft, and confirm it.
Identity, authorization, and consent questions are excluded from AI drafting.


### Streamed browser takeover and saved logins

Unfamiliar or login pages remain open with status `takeover`. Open the application
from **Applications** to go straight into the worker’s actual browser session:

1. While a run is preparing, open its live browser to watch the worker fill it.
   Control becomes available automatically after preparation pauses or finishes. Click a
   field and type directly; use your mouse wheel, trackpad, or a vertical swipe
   to scroll the employer page. Zoom is available for small screens.
2. Click supported sign-in, verification, Next/Back, or OAuth controls. Select a
   popup tab if the identity provider opens one. The view refreshes automatically.
3. After signing in, optionally choose **Remember this site login**. Then choose
   **Resume worker / review** to watch filling continue. Choose **Review answers & submit**
   when ready to inspect all recorded answers. Your browser session remains open.
4. For an unfamiliar form, **Use AI on this application page** maps fields to exact
   saved contact/professional facts. In Profile, you can separately enable this
   mapping during preparation. It requires a configured compatible model.
5. If the final button is unrecognized, enable **Mark final submit button** and click
   it in the image. Marking does not click it. Resume review and use the separate
   submission confirmation. The takeover controls block recognized final actions.

The selected frozen resume can be attached by enabling the upload mode and clicking
an actual file input/label. No arbitrary server filesystem path can be requested.
Live viewing uses Chromium screencast frames; interaction uses ordered requests
with automatic viewport refresh. It is near real time, not zero latency: network,
employer loading, and long worker/model operations can still delay input. Enter
is blocked to prevent implicit form submission; use the page’s Next button.
Live frames are held only in memory and cleared when the session closes.

Typed browser text is queued only in memory, not SQLite. Password/code values are
redacted from form snapshots. Live viewport images are returned in memory rather
than stored as login screenshots. Existing application screenshots remain local.

Saved browser state is encrypted with Fernet, expires locally after seven days,
and is reused only for the exact application origin. Cookies/local storage/IndexedDB
can retain authentication; device-bound credentials and some session-storage flows
may require another login. Streamed applications have isolated browser contexts.
Native tabs share cookies and browser storage, like tabs in a normal window.
Saved cookies/local storage from another site can be added to the shared session;
IndexedDB state for additional sites may require signing in again.
Use **Profile → Remembered site logins → Forget site login** to remove stored state;
this does not sign out an already-open context or revoke the employer's session.
The encryption key is stored with restricted permissions in the same local data
volume: it does not protect against someone who can read both the state and key.

These capabilities broaden coverage; they do not guarantee Google, Microsoft, or
any other specific site's current login flow. Some identity providers reject
browser automation or require unsupported device verification. No real account
login or employer submission is part of the test suite. AI plans are tested with
mock responses, not a configured live model. AI cannot execute JavaScript, arbitrary
selectors, navigation, or submission; its field references and source keys are
validated and mapped answers require review.

Implementation references: [Playwright authentication](https://playwright.dev/python/docs/auth),
[storage state](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state),
[Fernet](https://cryptography.io/en/latest/fernet/).

Live viewport implementation reference: [Playwright CDP sessions](https://playwright.dev/python/docs/api/class-cdpsession).

Native browser reference: [Playwright visible browser launch](https://playwright.dev/python/docs/library).
