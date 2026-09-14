# Contributing

Keep Apply Agent self-hosted and understandable: Python modules, SQLite, and the
bundled web interface. Run `python -m pytest -q` before proposing changes. Browser
changes also need `RUN_BROWSER_TESTS=1 python -m pytest -q` with Chromium installed.
Use synthetic job forms and temporary profiles; never submit real applications in
a test or include personal data/tokens in commits.

New source integrations belong in `src/jobs/`. Application platform adapters belong
in `src/applications/`. Keep unsupported sources and controls explicit. Preserve the
separate preparation and final-review steps, version-check final approval, and never
automatically retry a submission with an unknown outcome.

Do not infer work authorization, demographic answers, employment dates, or other
missing profile facts. Ask the user or leave those fields for review.
