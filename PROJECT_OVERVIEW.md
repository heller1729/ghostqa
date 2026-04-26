# GhostQA — Project Overview

## What's the idea?

The basic premise is pretty simple: what if you could point an AI at any website and it would just... use it? Click around, fill out forms, try weird inputs, and tell you when something breaks. No test scripts, no setup — just give it a URL.

That's GhostQA. It's an autonomous testing agent that explores web apps the way a real person would, except it's specifically trying to break things. Think of it like having a really thorough QA tester who never gets bored and always tries the edge cases nobody else thinks of.

## Why does this matter?

Right now, there's a gap in how web apps get tested:

- **Manual QA** catches things, but it's slow, expensive, and humans miss stuff.
- **Automated tests** (Cypress, Playwright, Jest) are great, but they only check what the developer thought to test. If nobody wrote a test for it, the bug slips through.
- **Neither approach finds the unknown unknowns.** The bugs you didn't anticipate.

GhostQA sits in that gap. It's not replacing Cypress or manual testing — it's covering the blind spots. The agent explores autonomously, tries things a scripted test wouldn't, and reports what looks wrong.

The reason this is possible *now* is because vision LLMs (like GPT-4o) can actually look at a screenshot of a webpage and understand what's on it — buttons, forms, error messages, broken layouts. A year ago this wasn't feasible. Now it is.

## How it works (the core loop)

The agent runs a simple loop that repeats until it's explored enough or hit the step limit:

```
1. OBSERVE  →  Take a screenshot, grab console logs
2. UNDERSTAND  →  Send screenshot to GPT-4o, ask "what's on this page?"
3. DETECT  →  Check for obvious issues (console errors, visual bugs)
4. DECIDE  →  Ask the LLM "what should I do next to find bugs?"
5. EXECUTE  →  Click a button, fill a form, navigate somewhere
6. RECORD  →  Save what happened for the bug report
7. → back to step 1
```

The key insight is that the LLM handles two jobs:
- **Vision** — understanding what's on screen (like reading a screenshot)
- **Reasoning** — deciding what to do next (like planning test cases)

These are separate calls because they need different prompts and contexts.

## What does it actually test?

For the first version, I'm focusing on things that are clearly detectable:

- **Form validation** — submitting empty forms, putting letters where numbers should go, extremely long inputs, special characters
- **Console errors** — JavaScript exceptions and uncaught errors that users never see but indicate real problems
- **Visual issues** — broken layouts, overlapping text, missing images, error pages shown to users
- **Navigation** — dead links, pages that don't load, redirect loops
- **Basic security stuff** — exposed stack traces, debug info visible to users

I'm explicitly *not* doing deep security scanning, performance testing, or accessibility checking in v1. Those are real products on their own and would blow up the scope.

## Project structure

```
ghostqa/
├── ghostqa/              # Main Python package
│   ├── __main__.py       # CLI — how you run it from the terminal
│   ├── agent.py          # The main loop described above
│   ├── browser.py        # Playwright wrapper — controls Chrome
│   ├── vision.py         # Talks to GPT-4o with screenshots
│   ├── reasoning.py      # Decides next actions, generates test inputs
│   ├── reporter.py       # Generates HTML/JSON bug reports
│   └── config.py         # Settings and configuration
├── tests/                # Unit tests
├── reports/              # Where bug reports get saved
├── requirements.txt
└── README.md
```

Each module has a clear job:

**browser.py** handles all the Playwright stuff — opening Chrome, taking screenshots, clicking elements, filling forms, capturing console errors. It's basically a clean API over Playwright so the rest of the code doesn't need to know browser automation details.

**vision.py** sends screenshots to GPT-4o and gets back structured data about what's on the page. It can also compare before/after screenshots to detect changes, and scan for visual bugs.

**reasoning.py** is the "brain." Given the current page state and the history of what's been done, it decides what to do next. It also generates test inputs for form fields (valid data, invalid data, edge cases, injection attempts).

**agent.py** ties everything together in the main loop. It coordinates the browser, vision, and reasoning modules, tracks state, and feeds bugs to the reporter.

**reporter.py** generates the final output — a styled HTML report with all the bugs found, reproduction steps, console errors, and summary statistics. Also saves a JSON version for programmatic access.

## Tech stack

| What | Why |
|------|-----|
| **Python 3.11+** | Main language. Async support, good LLM library ecosystem |
| **Playwright** | Browser automation. Better than Selenium for modern SPAs, handles dynamic content well |
| **OpenAI GPT-4o** | Vision + reasoning. Best available multimodal model for understanding screenshots |
| **Pydantic** | Data validation for configs and structured outputs |
| **Typer + Rich** | CLI interface with nice terminal output |
| **Jinja2** | HTML report templating |

I went with Playwright over Selenium because it handles single-page apps (React, Vue) much better — it waits for network activity to settle, handles dynamic content, and is generally more reliable with modern web apps.

For the LLM, GPT-4o is the obvious choice right now since it handles both vision and text reasoning in one model. The architecture is set up so I could swap in a different model later if needed.

## How a scan actually runs

From the user's perspective:

```bash
# Install and setup
pip install -r requirements.txt
playwright install chromium

# Create .env with your OpenAI key
echo "OPENAI_API_KEY=sk-..." > .env

# Run a scan
python -m ghostqa scan https://some-website.com --context "E-commerce site"
```

It opens a browser (headless by default, but you can watch it with `--no-headless`), navigates to the URL, and starts exploring. The terminal shows a progress spinner with the current step count. When it's done, it saves an HTML report to the `reports/` folder.

A typical scan of 50 steps takes maybe 5-15 minutes and costs roughly $0.50-1.00 in API calls.

## What I want to validate

The big questions I need to answer:

1. **Does GPT-4o actually understand web UIs well enough?** — My bet is yes for common patterns (forms, buttons, navigation), but edge cases with unusual layouts might be tough.

2. **Can the reasoning LLM make good exploration decisions?** — Will it get stuck in loops? Will it explore broadly enough? This is where most of the tuning will happen.

3. **Is the bug detection reliable?** — False positives are the killer here. If 40% of reported "bugs" aren't real bugs, the tool is useless. I need to keep false positives under 15%.

## Evaluation plan

I'm planning to test against:
- **OWASP Juice Shop** — a deliberately vulnerable web app with known bugs (good for measuring detection rate)
- **TodoMVC** — a clean, well-built app (good for measuring false positives)
- **A custom test app** — I'll build a small app with specific seeded bugs to benchmark against

The metrics I care about: bug detection rate (>80% of known bugs), false positive rate (<15%), and scan time (<30 min).

## Timeline

- **Weeks 1-2** (done): Project setup, basic structure
- **Weeks 3-4**: Get the vision + browser integration working end-to-end
- **Weeks 5-6**: Build the reasoning loop, start finding real bugs
- **Weeks 7-8**: Testing on multiple sites, prompt tuning
- **Weeks 9-10**: Polish reports, CLI, documentation
- **Week 11+**: IEEE report, final presentation

## Risks I'm watching

- **LLM costs spiraling** — Mitigating with caching and efficient prompts
- **Vision model hallucinating bugs** — Need careful prompt engineering and confidence thresholds
- **Exploration getting stuck** — Loop detection and maximum step limits as guardrails
- **Scope creep** — Keeping v1 focused on the core loop, everything else is "future work"

If the fully autonomous approach turns out to be too unreliable, my fallback is a semi-autonomous mode where the agent suggests actions and the user approves them. Still useful, still demonstrates the core AI concepts, just with a human in the loop.
