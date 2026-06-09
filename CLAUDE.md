# Vibedom - Project Context for Claude

## Project Overview

**Vibedom** is a hardware-isolated sandbox environment for running AI coding agents (Claude Code, Cursor, etc.) safely on Apple Silicon Macs.

**Current Status**: Phase 1 complete. Phase 2 DLP complete (real-time scrubbing, audit logging). Persistent container workflow complete (iterative development with bidirectional sync). Cloudflare AI Gateway integration complete (optional routing, auth headers, per-user attribution).

**Primary Goal**: Enable safe AI agent usage in enterprise environments with compliance requirements (SOC2, HIPAA, etc.)

## Architecture

### Core Components

1. **VM Isolation** (`lib/vibedom/vm.py`)
    - Supports both apple/container (preferred) and Docker (fallback)
    - Read-only workspace mount at `/mnt/workspace`
    - Agent workspace at `/work/repo` (bind-mounted from host — persists for persistent containers)
    - Claude Code CLI pre-installed with persistent config volume
    - Health check polling for VM readiness
    - `exists()` / `is_running()` / `pause()` / `restart()` for persistent container lifecycle

2. **Container State** (`lib/vibedom/container_state.py`)
   - `ContainerState` dataclass persisted as `~/.vibedom/containers/{name}/container.json`
   - Tracks workspace, runtime, proxy PID/port, status (running/stopped)
   - `ContainerRegistry` for discovering/looking up containers by workspace name or path

3. **Network Control** (`lib/vibedom/proxy.py`, `lib/vibedom/container/mitmproxy_addon.py`)
   - ProxyManager starts mitmproxy as a **host process** (not inside the container)
   - One process per container/session on an OS-assigned port (no hardcoded ports)
   - Port and PID stored in `container.json` or `state.json` for whitelist reload
   - Proxy auto-restarts on `vibedom up`/`vibedom shell` if PID is dead
   - Container receives `HTTP_PROXY=http://host.docker.internal:<port>` and CA cert via `/mnt/config/mitmproxy/`
   - Domain whitelist enforcement with subdomain support
   - DLP scrubber for secret and PII detection in outbound HTTP traffic
   - Logs all requests to `network.jsonl`

4. **Secret Detection** (`lib/vibedom/gitleaks.py`)
   - Pre-flight Gitleaks scan before VM starts
   - Risk categorization (critical vs warnings)
   - Interactive review UI for findings

5. **Session Management** (`lib/vibedom/session.py`)
   - Structured logging (JSONL for network, text for events)
   - Session directories: `~/.vibedom/logs/session-YYYYMMDD-HHMMSS-microseconds/`
   - Retained for ephemeral session workflow

6. **Global Config** (`lib/vibedom/global_config.py`)
   - `GlobalConfig` dataclass persisted at `~/.vibedom/config.json`
   - Stores Cloudflare AI Gateway credentials (account ID, gateway ID, auth token, username)
   - `extra_env()` → `ANTHROPIC_BASE_URL` injected into containers
   - `proxy_env()` → `VIBEDOM_CF_AIG_TOKEN` / `VIBEDOM_USER` injected into mitmproxy host process

7. **CLI** (`lib/vibedom/cli.py`)
   - **Persistent containers**: `up`, `down`, `destroy`, `status`, `shell`, `pull`, `push`
   - **Ephemeral sessions**: `run`, `stop`, `attach`, `review`, `merge`
   - **Shared**: `init`, `reload-whitelist`, `list`, `rm`, `prune`, `housekeeping`, `proxy-restart`
   - **Config**: `config cloudflare` — set/update/clear Cloudflare AI Gateway credentials

### Key Design Decisions

**Two container models**: Persistent (recommended) and ephemeral (retained for compatibility)
- Persistent: `vibedom up/down` — container survives across tasks (including reboots — stopped, not removed), repo bind-mounted from `~/.vibedom/containers/{name}/repo/`, sync via rsync
- Ephemeral: `vibedom run/stop` — fresh container per task, repo cloned on start, changes extracted as git bundle on stop

**Idempotent startup**: `startup.sh` skips git clone if `/work/repo/.git` exists
- Enables container restart without re-cloning
- SSH agent socket check prevents duplicate agents on restart

**Host-side rsync for sync**: `pull`/`push` rsync directly between host paths
- Container repo is a bind mount, so no `docker exec` needed
- `.gitignore` rules applied via `--filter=':- .gitignore'`
- `sync_exclude:` in `vibedom.yml` for project-specific additional excludes
- Additive by default (no `--delete`); destructive mode opt-in
- Path-specific sync uses `--relative` with rsync's `/.` anchor — directory structure preserved in destination

**Git Bundle Workflow** (ephemeral sessions): Git-native approach for agent changes
- Rationale: Cleaner code review, better GitLab integration, preserves commit history
- Implementation: Container clones workspace, creates git bundle at session end

**Explicit Proxy**: HTTP_PROXY/HTTPS_PROXY environment variables
- Rationale: Works with both HTTP and HTTPS
- Implementation: Environment variables set at container level, mitmproxy in regular mode
- Proxy port is baked into the container env at creation — restart must use same port
- Proxy auto-restart on `vibedom up` / `vibedom shell` if PID is dead

**Deploy Keys**: Unique SSH key per machine
- Rationale: Avoid exposing personal credentials to VM
- Setup: `vibedom init` generates key, user adds to GitLab/GitHub

**Cloudflare AI Gateway**: Optional routing of AI API calls through Cloudflare
- Configured during `vibedom init` or via `vibedom config cloudflare`
- Stored in `~/.vibedom/config.json` (account ID, gateway ID, auth token, username)
- `ANTHROPIC_BASE_URL` injected as container env var so Claude Code uses the gateway URL automatically
- Auth token and user ID injected as `cf-aig-authorization` / `cf-aig-metadata` HTTP headers by the mitmproxy addon (host process) — not stored in the container
- `gateway.ai.cloudflare.com` auto-added to the network whitelist on configuration

## Development Workflow

### Persistent Container Workflow (Primary)

**Setup (once per project):**
- Add `vibedom.yml` with `base_image`, `network`, `setup` commands, `sync_exclude`
- `vibedom up ~/projects/myapp` — creates container, runs setup commands

**Iterative development:**
```
vibedom shell myapp          # open shell, run agent inside
vibedom pull myapp src/      # pull agent's changes to host
# review, test, amend locally
vibedom push myapp src/      # push amendments back to container
# repeat
```

**Between tasks:**
- `vibedom down myapp` — stops container, environment preserved
- `vibedom up myapp` — restarts, no re-clone, proxy auto-restarts

### Ephemeral Session Workflow (Legacy / Isolated Tasks)

**Container Initialization:**
- Git workspaces: Cloned from host, checkout current branch
- Non-git workspaces: Fresh git init with snapshot commit
- Agent works in `/work/repo` (mounted to `~/.vibedom/logs/session-xyz/repo`)

**During Session:**
- Agent commits normally to isolated repo

**After Session:**
- Git bundle created at `~/.vibedom/logs/session-xyz/repo.bundle`
- User adds bundle as remote, reviews commits
- User merges into feature branch (with or without squash)
- User pushes feature branch for GitLab MR

### Git Worktrees

**Preferred location**: `.worktrees/` (hidden, project-local)

New features should be developed in isolated worktrees:
```bash
git worktree add .worktrees/feature-name -b feature-name
cd .worktrees/feature-name
```

### Test-Driven Development

**Always run the full test suite after any code change and fix any failures before committing.** Use `uv run pytest tests/ -v`. Core logic tests must be 100% passing.

All features follow TDD:
1. Write failing test
2. Run test to verify it fails
3. Implement minimal code to pass
4. Run test to verify it passes
5. Commit

**Test organization**:
- `tests/test_*.py` - Unit tests (no Docker required)
- Core logic: 100% passing
- Docker-dependent tests: May fail in sandboxed environments

### Code Quality Standards

- **DRY**: Extract constants, avoid magic numbers
- **YAGNI**: No speculative features
- **Error handling**: Contextual messages, proper exception chaining
- **Type hints**: Use throughout (except CLI functions use Click decorators)
- **Docstrings**: Include usage examples for public APIs

### Technical Debt Tracking

**Document deferred improvements in**: `docs/technical-debt.md`

**Current high-priority items**:
- File I/O error handling in network logging
- Input validation for log methods

## Known Limitations

### Persistent Container Sync

**Current Implementation:**
- Sync is manual — user runs `vibedom pull`/`vibedom push` explicitly
- No automatic file watching or live sync
- Full-tree sync requires confirmation (path-specific sync does not)

### Git Bundle Workflow (Ephemeral Sessions)

**Current Implementation:**
- Agent works on same branch as user's current branch
- Bundle contains all refs from session
- User decides to keep commits or squash during merge

### Proxy Mode

**Current Implementation**:
- Explicit proxy mode with HTTP_PROXY/HTTPS_PROXY environment variables
- Works for 95%+ of modern tools (curl, pip, npm, git)
- Tools that don't respect HTTP_PROXY may not be proxied

**Known edge cases**:
- Some legacy applications may ignore proxy environment variables
- Certificate-pinning applications (banking apps) will reject mitmproxy's CA
- Docker-in-Docker requires additional proxy configuration

**Mitigation**:
- Document tool-specific proxy configuration
- Create wrapper scripts for problematic tools if discovered
- Most AI agent workflows use standard tools that respect HTTP_PROXY

### Container Runtime

**Current**: Supports both apple/container (preferred) and Docker (fallback). Runtime is auto-detected at startup.

**Claude Code Persistence**: Uses a shared Docker volume (`vibedom-claude-config`) to persist authentication and settings across all workspaces.

**Future**: Enhancements to apple/container integration as the platform matures.

## Testing

### Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_gitleaks.py -v

# Run with coverage
uv run pytest tests/ --cov=lib/vibedom --cov-report=html
```

### Test Results (Phase 1)

- **Core logic**: 18/18 passing (100%)
- **Docker-dependent**: May fail without Docker daemon access
- **Coverage**: ~85%

### Manual Testing

```bash
# Build VM image
vibedom init  # builds image on first run

# Test persistent container workflow
vibedom up ~/projects/test-workspace
vibedom status
vibedom push test-workspace src/          # push a specific path
vibedom pull test-workspace src/ --dry-run
vibedom down test-workspace
vibedom up ~/projects/test-workspace      # should restart without re-cloning
vibedom destroy test-workspace --force

# Test ephemeral session workflow
vibedom run ~/projects/test-workspace

# Verify inside container
docker exec vibedom-<workspace> cat /tmp/.vm-ready
docker exec vibedom-<workspace> ls /work

# Test HTTP/HTTPS whitelisting
docker exec vibedom-<workspace> curl https://pypi.org/simple/

# Check logs (persistent)
cat ~/.vibedom/containers/test-workspace/network.jsonl

# Stop
vibedom stop ~/projects/test-workspace
```

## Project Structure

```
vibedom/
├── lib/vibedom/              # Core Python package
│   ├── cli.py               # Click CLI commands
│   ├── vm.py                # VM lifecycle management
│   ├── container_state.py   # Persistent container state (ContainerState, ContainerRegistry)
│   ├── session.py           # Ephemeral session logging
│   ├── project_config.py    # vibedom.yml parsing
│   ├── gitleaks.py          # Secret scanning
│   ├── review_ui.py         # Interactive review
│   ├── ssh_keys.py          # Deploy key management
│   ├── whitelist.py         # Domain whitelist logic
│   ├── proxy.py             # Host-side mitmproxy management
│   ├── global_config.py     # GlobalConfig: Cloudflare AI Gateway credentials, per-container env injection
│   ├── config/              # Default configs (gitleaks.toml, trusted_domains.txt)
│   └── container/           # Container image files (Dockerfile.*, startup.sh, mitmproxy_addon.py)
├── tests/                   # Test suite
├── docs/                    # Documentation
│   ├── ARCHITECTURE.md      # System architecture
│   ├── USAGE.md             # User guide
│   ├── TESTING.md           # Test documentation
│   └── technical-debt.md    # Deferred improvements
└── pyproject.toml           # Package configuration
```

## Common Commands

### Development

```bash
# Install in development mode
pip install -e .

# Run tests
uv run pytest tests/ -v

# Build VM image
vibedom init  # builds image on first run

# Check code style
ruff check lib/ tests/
```

### Usage

```bash
# First-time setup
vibedom init

# --- Persistent container workflow ---
vibedom up ~/projects/myapp          # start (or restart) container
vibedom shell myapp                  # open shell inside container
vibedom pull myapp src/              # sync container -> host (specific path)
vibedom push myapp src/              # sync host -> container (specific path)
vibedom pull myapp                   # sync all (respects .gitignore, asks confirmation)
vibedom status                       # show all container states
vibedom down myapp                   # stop (preserves filesystem)
vibedom destroy myapp                # remove container + data

# --- Ephemeral session workflow ---
vibedom run ~/projects/myapp         # start isolated session
vibedom attach                       # shell into session
vibedom stop                         # stop + create git bundle
vibedom review myapp-happy-turing    # inspect changes
vibedom merge myapp-happy-turing     # merge changes

# --- Shared ---
vibedom reload-whitelist             # hot-reload domain whitelist
vibedom list                         # list all sessions

# --- Cloudflare AI Gateway ---
vibedom config cloudflare --account-id abc123 --gateway-id my-gw   # configure
vibedom config cloudflare --account-id abc123 --gateway-id my-gw --auth-token TOKEN  # with auth
vibedom config cloudflare --clear    # remove gateway configuration
```

### Debugging

```bash
# Check container status
docker ps -a | grep vibedom
vibedom status

# View container logs
docker logs vibedom-<workspace>

# Open shell in container
vibedom shell myapp

# Check proxy health
cat ~/.vibedom/containers/myapp/container.json   # shows proxy_pid / proxy_port

# Check network logs (persistent containers)
cat ~/.vibedom/containers/myapp/network.jsonl

# Check network logs (ephemeral sessions)
cat ~/.vibedom/logs/session-*/network.jsonl
```

## Future Roadmap

### Phase 2: DLP and Monitoring ✅ (Complete)

- ✅ **DLP scrubbing**: Real-time secret and PII scrubbing in HTTP traffic
- ✅ **Shared patterns**: gitleaks.toml serves pre-flight scan + runtime DLP
- ✅ **Audit logging**: Scrubbed findings logged to network.jsonl

### Phase 2b: Persistent Containers ✅ (Complete)

- ✅ **Persistent containers**: `vibedom up/down/destroy` — container survives across tasks
- ✅ **Bidirectional sync**: `vibedom push/pull` with `.gitignore`-aware rsync
- ✅ **Setup scripts**: `setup:` in `vibedom.yml` for one-time environment setup
- ✅ **Proxy auto-restart**: proxy health checked and restarted on `up`/`shell`

### Phase 2c: Cloudflare AI Gateway ✅ (Complete)

- ✅ **Gateway routing**: `ANTHROPIC_BASE_URL` injected into containers so Claude Code routes API calls through Cloudflare
- ✅ **Auth headers**: `cf-aig-authorization` and `cf-aig-metadata` injected by mitmproxy addon (no credentials stored in container)
- ✅ **Init prompt**: `vibedom init` asks whether to configure gateway; `vibedom config cloudflare` for updates
- ✅ **Whitelist integration**: `gateway.ai.cloudflare.com` auto-added to trusted domains

### Phase 3: Production Hardening

- **High-severity alerting**: Real-time notifications for critical DLP findings
- **Log rotation**: Implement size limits and rotation policies
- **Session/container cleanup**: Automatic cleanup with retention policies
- **Multi-tenant support**: Workspace isolation for multiple users

## Contributing Guidelines

### Commit Messages

Follow conventional commits:
```
feat: add new feature
fix: bug fix
docs: documentation changes
test: test additions/changes
refactor: code refactoring
chore: maintenance tasks
```

Include co-authorship:
```
Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```

### Code Review Checklist

- [ ] Tests pass (core logic 100%)
- [ ] No hardcoded secrets or credentials
- [ ] Error handling with contextual messages
- [ ] Type hints on public functions
- [ ] Docstrings for non-trivial functions
- [ ] Technical debt documented if deferred

### Pull Requests

- Keep PRs focused (one feature/fix per PR)
- Update documentation for user-facing changes
- Add tests for new functionality
- Run full test suite before submitting

## Security Considerations

### Current Security Model

- **VM isolation**: Agent cannot escape to host via kernel exploits
- **Read-only workspace**: Original files protected from malicious writes
- **Forced proxy**: All traffic routed through mitmproxy (no bypass)
- **Deploy keys**: Unique SSH key per machine (not personal credentials)
- **DLP scrubbing**: Real-time secret/PII detection and scrubbing in HTTP traffic
- **Pre-flight scanning**: Gitleaks scan before VM starts
- **Sync path validation**: `pull`/`push` reject absolute paths and `../` traversal

### Known Security Limitations

- **Proxy bypass**: Tools that don't respect HTTP_PROXY can bypass whitelist (~5%)
- **HTTPS response scrubbing**: Currently only scrubs requests, not responses (by design - not a threat vector for prompt injection)

### Future Security Enhancements

- Kernel-level network filtering as fallback for non-proxy-aware tools
- High-severity alerting for critical DLP findings

## Support and Documentation

- **Architecture**: See `docs/ARCHITECTURE.md`
- **User Guide**: See `docs/USAGE.md`
- **Testing**: See `docs/TESTING.md`
- **Technical Debt**: See `docs/technical-debt.md`
- **Design Docs**: See `docs/plans/` for historical context

## Contact

For questions or issues, refer to project documentation or create an issue in the repository.

---

**Last Updated**: 2026-06-08 (Cloudflare AI Gateway integration complete)
**Status**: Phase 1 complete, Phase 2 DLP complete, Phase 2b persistent containers complete, Phase 2c Cloudflare AI Gateway complete
**Next Milestone**: High-severity alerting OR Phase 3 production hardening
