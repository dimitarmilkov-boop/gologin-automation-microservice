# GoLogin Automation Architecture

## Overview

The GoLogin automation service now follows a layered architecture that separates
API contracts, application services, infrastructure integrations, and
stand-alone tooling. Every module has a clearly scoped responsibility and lives
in a directory that reflects its layer:

```
app/
├── api/              # FastAPI routers, dependencies, response models
├── services/         # Application services, background workers, domain logic
│   └── gologin/      # GoLogin orchestration modules (async focused)
├── infrastructure/   # Integrations, adapters, CLI utilities, migrations
├── utils/            # Logging, exception hierarchy, shared helpers
└── main.py           # FastAPI entry point & service wiring

scripts/
└── gologin/          # Optional/legacy automation & maintenance scripts
```

The rest of this document dives into **every file** that moved as part of the
restructure, explaining what it does, why it resides in its folder, and how it
fits into the bigger picture.

---

## Application Service Layer (`app/services/gologin`)

These modules expose asynchronous APIs consumed by FastAPI routes, background
workers, and other services. They never perform blocking I/O directly; instead
they orchestrate infrastructure adapters or other async helpers.

### `service.py`

- **What it is:** The primary `GoLoginService` used by FastAPI.
- **Key responsibilities:**
  - Start/stop GoLogin profiles (`start_profile`, `stop_profile`).
  - Synchronize the profile catalog with the local database (`sync_profiles`).
  - Provide profile lookup by GoLogin profile name (`get_profile_by_name`).
  - Maintain concurrency control via an `asyncio.Semaphore`.
- **Why it belongs here:** This is the façade the rest of the application calls.
  It encapsulates domain rules (e.g., concurrent profile limits) while deferring
  HTTP calls and persistence to infrastructure code. Keeping it in `services/`
  makes dependency injection trivial and avoids leaking implementation details to
  API routes.

### `session_manager.py`

- **What it is:** A global coordinator that tracks live GoLogin/Selenium
  sessions.
- **Key responsibilities:**
  - Manage lifecycle of Selenium drivers attached to GoLogin profiles.
  - Ensure multiple orchestrators do not collide when referencing the same
    browser.
  - Provide convenience methods for acquiring/releasing sessions with proper
    cleanup.
- **Why it belongs here:** Session management is application logic that supports
  multiple workflows (authorization, monitoring, etc.). Placing it next to the
  service keeps the shared state and locking primitives in one async-aware
  module.

### `hybrid_manager.py`

- **What it is:** An orchestrator that decides how a profile should be run
  (local Chrome, remote GoLogin cloud, headless, etc.).
- **Key responsibilities:**
  - Inspect profile metadata and determine the correct execution strategy.
  - Delegate to infrastructure adapters for heavy lifting.
  - Expose async helpers consumed by the automation workflow.
- **Why it belongs here:** Strategy selection is business logic. The manager
  stitches together application configuration and infrastructure, so the glue
  naturally belongs in the service layer.

### `live_connector.py`

- **What it is:** Utilities for establishing live WebSocket/CDP connections to
  a running GoLogin profile.
- **Key responsibilities:**
  - Attach to debugging endpoints exposed by GoLogin.
  - Provide async wrappers for executing commands or reading browser state.
- **Why it belongs here:** Live inspection is needed by authorization and
  monitoring flows. Keeping the async connector beside the service ensures the
  rest of the app can reuse it without pulling in heavy infrastructure modules.

### `proxy_updater.py`

- **What it is:** Logic for updating and rotating proxies associated with
  GoLogin profiles.
- **Key responsibilities:**
  - Fetch latest proxy settings from external config (through infrastructure
    adapters).
  - Apply updates to active profiles and persist changes in the database.
- **Why it belongs here:** Proxy rotation is part of the domain rules for
  running GoLogin automation. The module coordinates updates without directly
  handling HTTP requests, making the service layer the right home.

### `monitors/session_monitor.py`

- **What it is:** Observability tooling for tracking profile health.
- **Key responsibilities:**
  - Collect usage metrics (active profiles, long-running sessions, failures).
  - Surface data to monitoring endpoints or background workers.
- **Why it belongs here:** Monitoring interacts closely with the service API and
  needs to read session state maintained by `session_manager.py`. Housing it in a
  `monitors/` subpackage keeps the concern close while still clearly separated.

### `__init__.py`

- **What it is:** Package initializer that re-exports relevant service types.
- **Why it belongs here:** Allows the rest of the application to import
  `GoLoginService` via `from app.services.gologin import GoLoginService`, hiding
  the internal file layout.

---

## Infrastructure Layer (`app/infrastructure/gologin`)

Modules here encapsulate blocking I/O, third-party SDK interactions, legacy code
paths, or CLI scripts. Application services depend on them; they should not
import from the service layer.

### `adapters/enhanced_manager.py`

- **What it is:** The most feature-complete integration with the GoLogin API and
  local cache.
- **Key responsibilities:**
  - Synchronous HTTP requests to GoLogin (start/stop, profile metadata).
  - Local SQLite caching or filesystem persistence as required by legacy logic.
  - Helper routines used by the hybrid manager when an advanced workflow is
    needed.
- **Why it belongs here:** The module performs blocking network I/O and tightly
  couples to GoLogin’s low-level API. Wrapping it as an adapter keeps the service
  layer async and mockable.

### `adapters/legacy_manager.py`

- **What it is:** The original GoLogin manager retained for backward
  compatibility.
- **Key responsibilities:**
  - Provide the old imperative interface relied on by long-lived scripts.
  - Act as a stepping stone while migrating to the new service façade.
- **Why it belongs here:** By labeling it as an infrastructure adapter, the code
  is isolated from new development. Once old pathways are retired, this module
  can be removed without touching application logic.

### `cli/setup.py`

- **What it is:** A command-line script to bootstrap GoLogin accounts,
  profiles, or other resources.
- **Key responsibilities:**
  - Runnable entry point for operators (e.g., `python -m app.infrastructure...`).
  - Calls into adapters to provision data and produces console output/logs.
- **Why it belongs here:** Operator tooling is infrastructure by nature. Keeping
  it under `cli/` makes it clear this is not part of the web service runtime.

### `migrations/migrate_enhanced.py`

- **What it is:** Data migration script for the enhanced manager’s storage.
- **Key responsibilities:**
  - Transform or seed local databases used by GoLogin automation.
  - Run as a stand-alone command when versions change.
- **Why it belongs here:** Migrations are part of infrastructure maintenance.
  Housing them next to adapters documents the coupling between the migration and
  the code that needs it.

### `__init__.py` (and subpackage initializers)

- **What they are:** Empty files allowing Python to treat the directories as
  packages.
- **Why they belong here:** They enable explicit imports such as
  `from app.infrastructure.gologin.adapters import enhanced_manager` without
  relying on implicit namespace packages.

---

## Scripts (`scripts/gologin`)

This directory contains tools that operators run manually. They may pull in
Selenium or other heavy dependencies that we do not want in the FastAPI runtime.

### `selenium_oauth_automation.py`

- **What it is:** A comprehensive Selenium script used before the service was
  introduced.
- **Key responsibilities:**
  - Drive the entire GoLogin → Twitter OAuth flow end-to-end.
  - Provide debugging utilities, screenshots, and manual control for operators.
- **Why it belongs here:** The script is still valuable for manual interventions
  but unnecessary for the web service. Moving it under `scripts/` keeps the
  runtime lean while preserving a path for human operators to execute the legacy
  flow.

---

## How the Pieces Interact

1. **FastAPI routes** (in `app/api/`) inject `GoLoginService` via dependency
   injection. The service exposes async methods that wrap infrastructure work.
2. **`GoLoginService`** orchestrates profile lifecycle, delegating blocking
   calls to adapters in `app/infrastructure/gologin`.
3. **Session helpers** such as `session_manager.py`, `live_connector.py`, and
   `proxy_updater.py` provide reusable logic for any workflow (authorization,
   monitoring, cleanup) without leaking Selenium specifics to the API layer.
4. **Infrastructure adapters** handle HTTP requests, CLI flows, and migrations
   while staying isolated from FastAPI.
5. **Scripts** continue to function independently. They can import the new
   service modules if needed, but by default they run standalone for maintenance
   tasks.

---

## Benefits of the Restructure

- **Clear layering:** Application logic lives in `services/`; infrastructure is
  isolated; scripts are optional.
- **Better testability:** Services can be unit-tested with mocked adapters;
  adapters can be integration-tested separately.
- **Safer evolution:** Retiring legacy modules becomes a matter of deleting a
  file in `infrastructure/` or `scripts/`, not refactoring the API layer.
- **Smaller runtime footprint:** The FastAPI app imports only the async-friendly
  modules it needs, reducing unwanted dependencies in production builds.

---

## Suggested Follow-Ups

- Update import statements throughout the project to reference the new package
  paths (e.g., `from app.services.gologin.service import GoLoginService`).
- Add or update unit tests to cover the reorganized service modules.
- Document run-books for operators that rely on the CLI or scripts.
- Audit infrastructure adapters to determine which legacy flows can be retired
  once the new service covers all use-cases.
