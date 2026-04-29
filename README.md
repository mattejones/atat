# ATAT — Application Tracking and Automation Tool

A personal applicant tracking system with an LLM-powered CV generation pipeline.
Drops a job description file, gets a tailored CV out. Built for people who treat
their job search the way they'd treat any other systems problem.

## Architecture

```
cv-library/     ← separate private repo — experience data, personas, skills
atat/           ← this repo — pipeline, tracking, frontend (eventually)
  jds/          ← drop JD files here to trigger the pipeline
  output/       ← generated CVs land here (gitignored)
  prompts/      ← LLM system prompts
  pipeline/     ← watcher, tailorer, renderer
  db/           ← schema and migrations
```

## Setup

### Prerequisites
- Python 3.11+
- A clone of your cv-library repo
- An Anthropic or OpenAI API key

### Installation

```bash
git clone https://github.com/yourusername/atat.git
git clone https://github.com/yourusername/cv-library.git

cd atat
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your paths and API key
```

### Running the watcher

```bash
python -m pipeline.watcher
```

Drop any plain text JD file into `/jds/` and ATAT will:
1. Classify the role and select the best-fit persona from your cv-library
2. Assemble your full experience context
3. Generate a tailored CV in Markdown
4. Write the output to `/output/{date}_{company}_{role}/`

## CV Library

ATAT expects a cv-library repo at the path specified in `CV_LIBRARY_PATH`.
The library should follow the structure documented in the cv-library README.

## Roadmap

- [x] JD drop folder watcher
- [x] LLM persona classification
- [x] Tailored CV generation (Markdown)
- [x] Typst → PDF rendering
- [ ] Web frontend / tracker UI
- [ ] PostgreSQL application tracking
- [ ] Email receipt sync
