# jobhunt-agent — Chrome extension

An in-browser panel (like Claude Cowork's) for your job hunt. It reads the job
you're viewing, scores fit against your profile, tailors your CV **truthfully**,
and saves the role to your funnel — all talking to a **local** engine. Nothing
leaves your machine.

## How it works
```
 Chrome side panel  ──HTTP──▶  bridge.py (localhost:8765)  ──▶  jobhunt engine
   (this folder)                 (../bridge.py)                (matcher/tailor/eval/tracker)
```
- `content.js` reads the JD off the page (read-only — never clicks or submits).
- The panel calls the local bridge for `/score`, `/tailor`, `/track`, `/apply`.
- **Apply-assist uses [Browser Use](https://github.com/browser-use/browser-use)**:
  it drives a real browser with your local Ollama model to pre-fill the form
  (dry-run stops before submit; auto-submit only on company ATS pages).
- The engine uses your local Ollama model; no cloud, no account automation.

## Setup
1. Start the engine (from the project root):
   ```bash
   pip install -r requirements.txt
   pip install browser-use langchain-ollama && playwright install chromium   # for apply-assist
   cp profile.example.md profile.md && cp profile.example.yaml profile.yaml   # edit these
   python bridge.py        # serves http://127.0.0.1:8765
   ```
2. Load the extension:
   - Chrome → `chrome://extensions` → enable **Developer mode**
   - **Load unpacked** → select this `extension/` folder
3. Open a job on Greenhouse / Lever / Ashby / LinkedIn / Indeed / Wellfound /
   Otta, click the toolbar icon, then **Analyze this job**.

## Safety
- Read-only on job pages; you always click apply/submit yourself.
- The panel only talks to `127.0.0.1` — your data and model stay local.
- On LinkedIn/aggregators it only *reads and tailors*; it never automates your
  account (that's what gets people banned).
