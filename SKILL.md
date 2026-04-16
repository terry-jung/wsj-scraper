---
name: wsj-scraper
description: Logs into WSJ with the user's credentials, searches a topic, filters by time period, summarizes each article with Claude API, and saves PDFs to ~/Desktop/WSJ_Articles/. Invoke with /wsj-scraper or when the user asks to scrape, search, or summarize WSJ articles.
user-invocable: true
version: 1.0.0
---

# /wsj-scraper — WSJ Article Scraper & Summarizer

The script lives at `~/Desktop/wsj_scraper.py` and runs inside the `wsj_scraper` conda environment.

Arguments passed: `$ARGUMENTS`

---

## What this skill does

1. Asks the user for any missing inputs (topic, time period, max articles) if not provided as arguments.
2. Runs `wsj_scraper.py` in the correct environment.
3. Scrapes articles, summarizes each with Claude, and emails an HTML digest to thjung91@gmail.com.
4. Each article in the digest includes: title (clickable), AI summary, and a visible "Read full article →" link to the original WSJ URL.

---

## Step 1 — Collect inputs

Parse `$ARGUMENTS` for the following (prompt for anything missing):

| Parameter | Description | Default |
|---|---|---|
| `topic` | Search topic (e.g. "Federal Reserve interest rates") | — (required) |
| `period` | Time filter: `1d`, `3d`, `1w`, `2w`, `1m`, `3m`, `6m`, `1y`, `all` | `1w` |
| `max` | Max number of articles to scrape | `5` |

Examples of valid invocations:
- `/wsj-scraper` → prompt for all inputs interactively
- `/wsj-scraper topic="AI chips" period=1w max=10`
- `/wsj-scraper Federal Reserve` → treat the whole argument as the topic, use defaults

---

## Step 2 — Check prerequisites

Before running, verify:

1. **Script exists**: `~/Desktop/wsj_scraper.py`
   - If missing, tell the user and offer to recreate it.

2. **Conda env exists**: `/Users/terryjung/opt/anaconda3/envs/wsj_scraper`
   - If missing: `conda create -n wsj_scraper python=3.11 -y && /Users/terryjung/opt/anaconda3/envs/wsj_scraper/bin/pip install playwright anthropic && /Users/terryjung/opt/anaconda3/envs/wsj_scraper/bin/python -m playwright install chromium`

3. **Anthropic API key**: check if `ANTHROPIC_API_KEY` is set in the environment.
   - If not set, remind the user they will be prompted for it when the script runs.

---

## Step 3 — Run the script

Tell the user to run the following command in their terminal (the script is interactive — it prompts for credentials):

```bash
conda activate wsj_scraper && python ~/Desktop/wsj_scraper.py
```

Or, if you can run shell commands directly in this session, execute:

```bash
/Users/terryjung/opt/anaconda3/envs/wsj_scraper/bin/python ~/Desktop/wsj_scraper.py
```

The script will interactively prompt for:
- WSJ email
- WSJ password
- Anthropic API key (if `ANTHROPIC_API_KEY` env var is not set)
- Search topic (if not pre-filled)
- Time period (if not pre-filled)
- Max articles (if not pre-filled)

---

## Step 4 — Report results

After the script finishes, tell the user:

```
Done! Digest emailed to thjung91@gmail.com
```

If the script errored, show the error and suggest fixes:

| Error | Likely cause | Fix |
|---|---|---|
| No articles found | Topic too narrow or wrong time period | Broaden topic or extend time period |
| Summarization error | Invalid or missing API key | Check ANTHROPIC_API_KEY in script |
| Email send error | App password expired | Generate new Gmail App Password |
| Bot detection | Chrome cookies expired | Log in to wsj.com in Chrome again |

---

## Quick reference

- **Script location**: `~/Desktop/wsj_scraper.py`
- **Email recipient**: `thjung91@gmail.com`
- **Python env**: `/Users/terryjung/opt/anaconda3/envs/wsj_scraper`
- **Time period options**: `1d` `3d` `1w` `2w` `1m` `3m` `6m` `1y` `all`
- **Session**: Auto-reads from Chrome (Profile 2) — re-login to wsj.com in Chrome if cookies expire
