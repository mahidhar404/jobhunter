# FormFactory Offline Gym

Local FormFactory integration for scoring fastfill against realistic HTML forms **without submitting data externally**. All fills use dummy applicant data (Ada Lovelace) — never `profile.json` or real credentials.

## Layout

```text
scripts/fastfill/gym/formfactory/
├── README.md
├── vendor/                  # FormFactory clone (or minimal stub)
│   ├── templates/           # HTML forms
│   └── data/data1/          # Gold JSON field schemas
└── ../formfactory_runner.py # CLI + run_formfactory_gym()
```

### Vendor: full clone vs stub

**Full clone** (preferred):

```bash
git clone --depth 1 https://github.com/formfactory-ai/formfactory \
  scripts/fastfill/gym/formfactory/vendor/
```

**Minimal stub** (offline fallback): if the clone fails, create `vendor/templates/` with three simple forms (`job_app.html`, `registration.html`, `contact.html`) and matching gold JSON under `vendor/data/data1/`. Re-clone when network is available.

## Smoke vs full

| Mode | Flag | Forms | Use case |
|------|------|-------|----------|
| **Smoke** | `--self-test` | First 3 discovered templates | CI / quick regression (~30s) |
| **Full** | `--full` | All templates with gold JSON | Benchmark sweep before releases |

Both modes:

1. Discover template + gold pairs
2. Serve HTML locally (`set_content` or loopback `http.server`)
3. Fill fields with the dummy map via Playwright
4. Score by reading the DOM (no POST/submit off-machine)

## Dummy profile

Fixed gym values only:

- Name: **Ada Lovelace**
- Email: `ada.lovelace+gym@example.com`
- Phone: `+1-555-0100`
- Other fields: deterministic placeholders derived from gold field names

## CLI

```bash
# Smoke test (exit 0 when all 3 pass)
skyvern_runtime/venv/bin/python scripts/fastfill/gym/formfactory_runner.py --self-test

# List discovered cases
skyvern_runtime/venv/bin/python scripts/fastfill/gym/formfactory_runner.py --list

# Full benchmark
skyvern_runtime/venv/bin/python scripts/fastfill/gym/formfactory_runner.py --full
```

## Python API

```python
from scripts.fastfill.gym.formfactory_runner import run_formfactory_gym

summary = run_formfactory_gym(smoke=True)
# {"ok": True, "n": 3, "passed": 3, "field_accuracy": 1.0, "cases": [...]}
```

## Safety

- Forms are stripped of `method="POST"` / external actions before fill
- Submit buttons are disabled (`type="button"`)
- Scoring reads input values from the DOM only — no network submission
