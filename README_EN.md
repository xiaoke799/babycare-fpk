# BabyCare (BabyCare-FPK) - Native Parenting App for fnOS

**Language / 语言**: [简体中文](README.md) | [English](README_EN.md)

## Introduction

BabyCare (育儿宝) is a native NAS parenting app for the fnOS platform (by fnOS / 飞牛), designed for families running a fnOS NAS. It is **local-first**: all parenting data — growth records, feeding logs, diaries, photos — stays entirely on your own NAS. Nothing is uploaded to third-party clouds, so your family's parenting data remains a truly private digital space.

## Core Features

### 📊 Growth Records
- Height, weight and head-circumference tracking with WHO growth curves
- Automatic BMI calculation and growth-velocity tracking
- Side-by-side photo management

### 🍼 Care Tracking
- Feeding logs (breast milk / formula / solids, with intake amount and nursing side)
- Sleep logs (sessions, quality, automatic statistics)
- Diaper change logs (wet / dirty / mixed)
- Pumping logs and tummy-time training records

### 🏥 Health Records
- Checkup records, temperature monitoring, medication logs
- Allergy tests, fontanelle checks, teething records
- Vaccine management and medication reminders

### 🎯 Development Assessment
- Developmental milestone tracking
- ASQ developmental screening
- Developmental leap tracking

### ✍️ Parenting Diary
- Capture your baby's growth moments
- Upload comparison photos
- Diary templates and AI-assisted generation

### 🤖 AI-Assisted Parenting
- Smart summaries of growth records
- Parenting suggestions
- Photo content description
- Diary generation
- Q&A

### 📚 Parenting Knowledge Base
- Built-in articles on feeding, sleep, development, health and care
- Solid-food recipe recommendations

### ⏱️ Tools
- Feeding timer, sleep timer
- Data export / import

## AI Features

BabyCare can integrate LLM capabilities in two modes:

### 1. Online API Mode
Calls external online LLM services (e.g. DeepSeek, Kimi, OpenAI).

⚠️ **Privacy notice**: when a third-party online API is selected, data such as baby photos, diaries and feeding logs is transmitted to that provider's servers and leaves your NAS. Use with care for highly sensitive information.

### 2. Locally Deployed LLM Mode (recommended, privacy-first)
The model runs on the NAS hardware itself; all AI inference stays local. Baby data never leaves your device, fully preserving the local privacy advantage.

See the [Privacy Policy](PRIVACY.md) and [User Agreement](USER_AGREEMENT.md) for details.

## Tech Stack

- **Backend**: Python Flask + Gunicorn + SQLite
- **Frontend**: vanilla HTML/CSS/JavaScript (responsive design)
- **Access**: fnOS gateway (Unix Socket)
- **Storage**: SQLite database + fnOS data directory
- **AI**: OpenAI-compatible APIs and local Ollama

## Project Structure

```
babycare-fpk/
├── manifest              # App package descriptor
├── NOTICE                # Third-party components & data attribution
├── README.md             # Project intro (Chinese)
├── USER_AGREEMENT.md     # User agreement
├── PRIVACY.md            # Privacy policy
├── LICENSE               # Apache-2.0
├── cmd/                  # Lifecycle scripts (main/install/upgrade/uninstall/config)
├── config/               # Runtime permissions & resources
├── wizard/               # Install/uninstall wizard
├── app/
│   ├── backend/          # Python backend
│   │   ├── server.py     # Flask entry point
│   │   ├── ai_engine.py  # AI engine (local rule engine + LLM integration)
│   │   ├── growth_utils.py / who_data.py   # WHO growth standards math & data
│   │   ├── blueprints/   # Route modules
│   │   └── vendor/       # Offline dependencies (Flask & co., see NOTICE)
│   ├── frontend/         # Frontend pages (vanilla HTML/CSS/JS, responsive)
│   ├── ui/               # fnOS desktop entry
│   └── data/             # Data directory (generated at runtime)
├── ICON.PNG              # App icon
└── ICON_256.PNG          # App icon (256x256)
```

## Local Development

```bash
cd app/backend
pip install -r requirements.txt
python3 server.py    # default http://localhost:8090, override with BABYCARE_PORT
```

Local development mode disables passwordless login by default: set the environment
variable `BABYCARE_DEV_AUTH=1`, then log in via `POST /api/auth/login`. On fnOS
production the gateway handles authentication, so this switch is not needed.

## Packaging

```bash
fnpack build -d babycare-fpk
# produces babycare-fpk.fpk
```

Note: when packing on Windows, the exec bits of the lifecycle scripts inside the
package must be fixed to 0755 (Windows filesystems do not preserve Unix exec
bits), otherwise the scripts cannot run on the fnOS device.

## License

This project is licensed under the [Apache License 2.0](LICENSE). Copyright 2026 xiaoke799.

## Privacy Policy

See the [Privacy Policy](PRIVACY.md). All data is stored locally; when online AI
features are used, some data may be sent to third-party providers.

## User Agreement

See the [User Agreement](USER_AGREEMENT.md).

## Data Compliance

- WHO growth reference data: [WHO Child Growth Standards](https://www.who.int/tools/child-growth-standards)
- Health encyclopedia content is for reference only and does not constitute medical advice
- When using online AI features, comply with the terms of the corresponding AI provider

## Versioning

Version numbers follow the `manifest` file and git tags; git commits and package
versions are independent — several development milestones may be merged into a
single release version.

- v0.0.1 (2026-09-13) - First open-source release (git tag v0.0.1)
