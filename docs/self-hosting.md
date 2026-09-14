# Self-hosting and phone review

Apply Agent is one person's installation. It does not need product accounts,
a hosted SaaS subscription, or a browser extension.

## Run on your computer

```sh
docker compose up -d --build
```

Open `http://localhost:8000` and complete setup. Keep Docker and the computer running
for alerts and preparation. Configure Telegram directly in the interface; its token
is stored in the local SQLite file and never returned by the settings API.

## Run on a server

Copy or clone the project onto a Linux server with Docker Compose, then use the
same command. The named `agent-data` volume contains the database, settings, resumes,
and review screenshots. `restart: unless-stopped` restarts the app after process
failure or server reboot when Docker starts. Keep the Compose project name stable.

No inbound port is needed for Telegram; it uses outbound HTTPS long polling.
The web interface is published only on the server's loopback address by default.
Do not expose this single-user app directly to the public internet.

For laptop administration, forward the port through SSH:

```sh
ssh -L 8000:127.0.0.1:8000 user@server
```

Then open `http://localhost:8000` on that laptop. For phone access, use a private
VPN or a phone SSH client with local port forwarding. Open the forwarded local URL
in the phone browser. A desktop SSH tunnel alone does not make the app reachable
on the phone.

If you use a private reverse proxy/hostname instead, route it to port 8000 over a
protected network and add only that hostname to `APPLY_AGENT_HOSTS` in `.env`:

```dotenv
APPLY_AGENT_HOSTS=my-private-hostname
```

Set **Preferences → Private review URL** to the address your phone can reach.
Telegram review messages then include a link to the correct application. This
setting generates links; it does not set up networking or publish the app.
The proxy must forward the intended Host header; cross-origin mutation requests
are rejected. HTTPS is recommended whenever traffic leaves loopback.

## Telegram setup

1. Use the official [@BotFather](https://t.me/BotFather) to create a dedicated bot.
2. Paste its token into the Telegram page in Apply Agent.
3. Open the pairing link and press Start in your private chat within ten minutes.
4. Check that the interface shows the connected bot.

Only possession of the expiring pairing code links an account; unrelated `/start`
messages cannot claim it. The user ID and private chat must both match on approvals.
Use one bot for one installation. A bot with an existing webhook is rejected rather
than silently reconfigured. The app uses long polling, not webhook delivery.

A source's first successful check establishes a baseline and sends one summary.
Only later unseen URLs can alert. Keyword filters affect future discoveries, not
past alerts; imported jobs remain browsable. Duplicate taps cannot create duplicate
queue entries. Notifications use at-least-once delivery: a crash after Telegram
accepts a message but before local acknowledgement can cause a duplicate message.

## Application review

Approving a supported job allows the browser worker to send your selected profile
facts and resume to that employer's form. It pauses before final submission. Open
the application in your workspace, review the screenshot and answers, and edit
fields as needed. Dropdown choices are read from the actual employer control.
Screening answers, consents, and demographic answers are never guessed.

The final button opens a confirmation dialog. Confirming requests submission of
that specific revision. Any live form change or missing required answer stops it
and returns the application for review. No worker automatically retries submission.
A confirmed employer acknowledgement marks it submitted; an uncertain outcome is
shown as unknown. Check email/the employer before doing anything else in that case.

The review screen is a mobile interface to the server's live browser, not a browser
session copied to your phone. Screenshots/standard controls work there. This version
provides a live browser viewport with supported clicks, typing, scrolling, and tab
switching. It does not stream a full remote desktop; arbitrary canvas interactions,
some CAPTCHA widgets, and device-bound login flows may still need another adapter.

## Upgrade from the earlier worker-only scaffold

Stop the old services first, retaining the existing volume:

```sh
docker compose down
# After updating the project files:
docker compose up -d --build --remove-orphans
```

Use the same project directory/name so Compose reuses `agent-data`. Existing jobs,
profiles, queue entries, Telegram history, and baseline data remain. New tables are
added; old queue rows are extended through a separate application-runs table.

The earlier `.env` bot token and JOB_SOURCES settings are no longer read by the
integrated app. Link your bot in the UI and add/enable desired sources there.
Remove obsolete secrets from `.env` after linking. Do not run the old
`src.telegram.worker` process alongside the app; the shared worker lock rejects it.

## Operations

```sh
docker compose logs --tail=100 -f app
docker compose ps
docker compose down
```

`down` retains data; `down -v` erases the volume. Stop the app before copying its
volume for a consistent backup. Protect those copies: they contain your profile,
bot token, optional model API key, resume files, and application screenshots. Local files are not encrypted
by the application; rely on your device/server's storage and access protections.

Streamed browser sessions are held in memory for two hours, with five slots by default (1–20 in Preferences). Native applications share one browser window and remain open until closed or the worker stops.
Queue selections can contain hundreds of jobs; new jobs start as slots become free. A restart
expires live reviews and discards pending browser commands. Jobs whose submission
was in progress become unknown rather than queued for retry. Browser contexts use
fresh isolated sessions; your normal Chrome profile is never attached.

Docker builds install their own compatible Chromium. Native development can use
`python -m playwright install chromium`, or `BROWSER_CHANNEL=chrome` with installed
Chrome. The OS worker lock is supported on macOS/Linux; use Docker on Windows.

Hosting costs and free-tier limits depend on where you run the app. No server or
cloud account is provisioned by this project.

References: [Telegram Bot API](https://core.telegram.org/bots/api),
[Docker installation](https://docs.docker.com/engine/install/),
[Playwright browsers](https://playwright.dev/python/docs/browsers).


## Browser takeover and credentials

The browser control API is private-installation only, like the rest of this app.
Use a trusted private connection and HTTPS when accessing it across a network.
Passwords and MFA text use a bounded in-memory queue; they are not saved as SQLite
commands. Browser snapshots redact password/code controls. Live login images are
served as transient response data and should not be logged by a reverse proxy.

Opt-in saved sessions live in `data/browser-sessions` as encrypted `.enc` files.
The local `key` file is mode 0600 and the directory mode 0700. Backups containing
both can decrypt the sessions, so protect the whole volume. These files are already
excluded from Git and Docker build context by the existing data-directory rules.
Sessions are scoped to exact application origin and expire locally after seven days.
The app does not import your normal Chrome profile, store typed passwords, or bypass
identity-provider verification. Forgetting a saved session leaves existing open
browser contexts active until closed.

Upgrade native installations with `python -m pip install -r requirements.txt` to
install the cryptography dependency. Docker builds install it automatically. Finish
live reviews before restarting; takeover sessions also expire on restart.

## Desktop versus server browsers

`python main.py` chooses visible Chromium windows on a local desktop. Docker and
ASGI server entry points default to `BROWSER_MODE=stream` for headless operation.
Set `BROWSER_MODE=stream` explicitly for remote/phone access. Native mode requires
a graphical desktop and focuses a window on that host, never on a remote client.
Changing modes requires restarting and preparing applications again. Desktop
windows remain owned by the worker; closing the app or restarting ends them.

Native mode shares browser storage across application tabs. Learning is on by default
and can be disabled in Preferences. Manual answers and an application change history
are stored in SQLite; the native UI does not generate or stream previews. Opening
large batches as desktop tabs uses more browser memory. Streamed mode retains its
existing session limit.
