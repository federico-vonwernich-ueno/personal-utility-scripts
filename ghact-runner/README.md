# ghact-runner

A Python automation tool for batch-testing GitHub Actions workflows across multiple repositories locally using [nektos/act](https://github.com/nektos/act).

## Overview

`ghact-runner` allows you to:
- **Clone** multiple GitHub repositories in bulk
- **Inject** a standardized GitHub Actions workflow into each one
- **Run** workflows locally in Docker containers (no commits/pushes to GitHub)
- **Track** results and collect detailed logs
- **Notify** your team via Slack with comprehensive reports

This is ideal for pre-deployment validation, security auditing, compliance checking, and testing workflow changes across an entire organization.

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Slack Integration](#slack-integration)
- [How It Works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Examples](#examples)

---

## Features

- ✅ **Batch Repository Processing**: Clone and process dozens or hundreds of repositories
- ✅ **Flexible Repo Formats**: Support for `org/repo`, HTTPS, and SSH URLs
- ✅ **Local Execution**: Run GitHub Actions workflows locally with `act` (no remote pushes)
- ✅ **Smart Updates**: Existing repos are updated atomically instead of re-cloned
- ✅ **Comprehensive Logging**: Each repo gets timestamped logs, all zipped for analysis
- ✅ **Slack Notifications**: Automated start/end notifications with detailed reports
- ✅ **Dry-Run Mode**: Test your configuration safely without making changes
- ✅ **Error Handling**: Choose to continue or stop on first failure
- ✅ **Custom Workflows**: Use external files or inline YAML

---

## Requirements

### Required Tools

1. **Python 3.8+**
2. **GitHub CLI (`gh`)** - [Installation guide](https://cli.github.com/)
   - Must be authenticated: `gh auth status`
3. **Git** - For repository management
4. **Docker** - Required by `act` to run workflow containers
5. **act** - [Installation guide](https://github.com/nektos/act#installation)
6. **PyYAML** - Install with: `pip install pyyaml`

### Optional Dependencies

For Slack notifications (optional):
- `slack-sdk` - Install with: `pip install slack-sdk`
- Requires companion script: `scripts/slack-notifier/slack_notifier_sdk.py`

---

## Installation

1. **Install system dependencies:**
   ```bash
   # GitHub CLI (example for Ubuntu/Debian)
   curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
   echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
   sudo apt update
   sudo apt install gh

   # Docker (if not installed)
   sudo apt install docker.io
   sudo usermod -aG docker $USER  # Add yourself to docker group

   # act
   curl https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash
   ```

2. **Authenticate with GitHub:**
   ```bash
   gh auth login
   ```

3. **Install Python dependencies:**
   ```bash
   pip install pyyaml

   # Optional: for Slack notifications
   pip install slack-sdk
   ```

4. **Download the script:**
   ```bash
   curl -O https://path-to-your-script/ghact-runner
   chmod +x ghact-runner.py
   ```

---

## Configuration

Create a YAML configuration file (e.g., `repos.yml`):

```yaml
# Directory where repositories will be cloned
checkout_dir: ~/ghact-repos

# Repositories to process
repos:
  # Simple format: just the repo URL or org/repo
  - owner/repo-name
  - https://github.com/org/another-repo

  # Advanced format: with custom name and branch
  - url: owner/special-repo
    name: custom-dir-name
    branch: develop

# Workflow to inject (choose ONE method)

# Method 1: External file
workflow_file: ./my-workflow.yml

# Method 2: Inline YAML
workflow_inline: |
  name: Local CI Check
  on: [push]
  jobs:
    test:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - name: Run tests
          run: |
            echo "Running tests..."
            npm test

# Workflow filename (placed in .github/workflows/)
workflow_filename: local-ci.yml

# Act configuration
act_event: push  # Event to trigger (push, pull_request, etc.)

# Optional: custom platform mappings for act
platform_mappings:
  ubuntu-latest: catthehacker/ubuntu:act-latest
  ubuntu-22.04: catthehacker/ubuntu:act-22.04

# Optional: extra arguments passed to act
act_args:
  - --verbose
  - --env-file
  - .env.local

# Error handling
continue_on_error: true  # true = process all repos, false = stop on first failure
```

See [`repos.example.yml`](repos.example.yml) for more detailed examples.

---

## Usage

### Basic Usage

```bash
python ghact-runner.py --config repos.yml
```

### Dry-Run Mode

Test your configuration without making any changes:

```bash
python ghact-runner.py --config repos.yml --dry-run
```

### Custom Tool Paths

If `gh` or `act` are not on your PATH:

```bash
python ghact-runner.py --config repos.yml \
  --gh-path /usr/local/bin/gh \
  --act-path /opt/bin/act
```

### Command-Line Options

```
--config PATH       Path to YAML config file (default: repos.yml)
--gh-path PATH      Custom path to gh executable
--act-path PATH     Custom path to act executable
--dry-run           Show what would happen without executing
```

---

## Slack Integration

### Setup

1. **Create a Slack App** with Bot Token Scopes:
   - `chat:write`
   - `files:write`
   - `channels:read`

2. **Set environment variables:**
   ```bash
   export SLACK_BOT_TOKEN="xoxb-your-token-here"
   export SLACK_CHANNEL="#ci-notifications"
   ```

3. **Optional: Test mode**
   ```bash
   export SLACK_DRY_RUN=1  # Simulates Slack notifications without sending
   ```

### What Gets Sent

**Start Notification:**
- Repository count
- List of repositories (as attached file)
- Start timestamp

**End Notification:**
- Success/failure counts
- Execution duration
- Failed repos with exit codes
- Complete logs (zipped)
- Success/failure lists (as text files)

### Notification Templates

The script uses templates for consistent formatting:
- `simple` - Basic information layout
- `workflow_success` - Success-themed formatting
- `workflow_failure` - Failure-themed formatting with alerts

---

## How It Works

### Execution Flow

```
1. Load Configuration
   ├─ Parse YAML file
   ├─ Validate required fields
   └─ Resolve paths

2. Verify Tools
   ├─ Check gh, git, docker, act
   └─ Exit if any missing

3. Clear Logs Directory
   └─ Remove old logs for clean slate

4. Send Start Notification (if Slack configured)

5. For Each Repository:
   ├─ Create timestamped log file
   ├─ Clone or Update Repository
   │  ├─ New: gh repo clone --depth 1
   │  └─ Existing: atomic temp-dir update
   ├─ Inject Workflow
   │  └─ Write to .github/workflows/{workflow_filename}
   ├─ Run Act
   │  └─ act -W .github/workflows/{workflow_filename} {event}
   └─ Track Result (success/failure)

6. Generate Summary
   ├─ Count successes and failures
   ├─ Calculate execution duration
   └─ Collect all log files

7. Send End Notification (if Slack configured)
   ├─ Zip all logs
   ├─ Attach success/failure lists
   └─ Include detailed summary

8. Exit
   └─ Code 0 if all succeeded, 1 if any failed
```

### Repository Update Strategy

**For new repositories:**
- Shallow clone with `--depth 1` for efficiency
- Uses `gh repo clone` for consistent authentication

**For existing repositories:**
- Creates temporary directory
- Clones fresh copy into temp
- Atomically swaps directories (prevents corruption)
- No risk of merge conflicts or dirty working trees

### Logging

Each repository gets a dedicated log file:
```
logs/
├── repo-name-1/
│   └── 20251012-162230.log
├── repo-name-2/
│   └── 20251012-162245.log
└── repo-name-3/
    └── 20251012-162301.log
```

All logs include:
- Clone/update output
- Workflow injection confirmation
- Complete `act` execution output
- Final success/failure status

---

## Troubleshooting

### Common Issues

**1. "gh: command not found"**
- Install GitHub CLI: https://cli.github.com/
- Or specify path: `--gh-path /path/to/gh`

**2. "gh authentication required"**
```bash
gh auth login
```

**3. "act: command not found"**
- Install act: https://github.com/nektos/act#installation
- Or specify path: `--act-path /path/to/act`

**4. "Docker daemon not running"**
```bash
sudo systemctl start docker
sudo usermod -aG docker $USER  # Then logout/login
```

**5. "PyYAML import error"**
```bash
pip install pyyaml
```

**6. Act workflows fail with permission errors**
- Check your workflow doesn't require `GITHUB_TOKEN` for write operations
- Add secrets via act's `--secret-file` option in `act_args`

**7. Slack notifications not sending**
- Verify `SLACK_BOT_TOKEN` and `SLACK_CHANNEL` are set
- Check bot has permissions in target channel
- Use `export SLACK_DRY_RUN=1` to test without sending

**8. "Repository already exists" errors**
- The script handles existing repos automatically
- If update fails, manually delete the repo directory and retry

### Debug Mode

For verbose output, add to your `act_args` in config:
```yaml
act_args:
  - --verbose
```

Check individual log files in `logs/` for detailed error messages.

---

## Examples

### Example 1: Security Audit Across Organization

```yaml
checkout_dir: ~/security-audit
repos:
  - myorg/frontend
  - myorg/backend
  - myorg/mobile-app
  - myorg/infrastructure

workflow_inline: |
  name: Security Scan
  on: [push]
  jobs:
    security:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - name: Run Trivy
          run: |
            docker run --rm -v $(pwd):/scan aquasec/trivy:latest fs /scan
        - name: Check dependencies
          run: |
            npm audit || true
            pip-audit || true

workflow_filename: security-scan.yml
act_event: push
continue_on_error: true
```

### Example 2: Lint All Repos

```yaml
checkout_dir: ~/lint-check
repos:
  - company/repo1
  - company/repo2
  - company/repo3

workflow_file: ./lint-workflow.yml
workflow_filename: lint.yml
act_event: push

platform_mappings:
  ubuntu-latest: catthehacker/ubuntu:act-latest

act_args:
  - --pull=false  # Don't pull images if already cached

continue_on_error: true
```

### Example 3: Test Specific Branches

```yaml
checkout_dir: ~/branch-test
repos:
  - url: myorg/project-a
    branch: feature/new-ci
  - url: myorg/project-b
    branch: develop
  - url: myorg/project-c
    branch: main

workflow_inline: |
  name: Branch Test
  on: [push]
  jobs:
    test:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - run: echo "Testing branch ${{ github.ref }}"

workflow_filename: test.yml
act_event: push
continue_on_error: false  # Stop on first failure
```

---

## Architecture

### Key Components

**Configuration (`Config` dataclass):**
- Repos list with URL/branch/name
- Workflow source (file or inline)
- Act settings and platform mappings
- Error handling behavior

**Repository Handling:**
- `clone_with_gh()` - Fresh clone via GitHub CLI
- `update_existing_repo_with_gh()` - Atomic update strategy
- `repo_dir_name_from()` - Extract directory name from URL

**Workflow Management:**
- `write_workflow()` - Inject workflow into `.github/workflows/`
- Supports both external files and inline YAML

**Execution:**
- `run_act()` - Execute workflow with act
- `run()` - Subprocess wrapper with logging

**Logging:**
- `RunLogger` class - Dual output (console + file)
- Real-time streaming of subprocess output
- Automatic directory creation and cleanup

**Notifications:**
- `send_slack_notification()` - Unified Slack sender
- `_send_start_notification()` - Initial notification with repo list
- End notification with comprehensive summary and logs

### Design Principles

1. **Idempotency**: Safe to run multiple times
2. **Atomicity**: Repository updates use temp-dir-swap pattern
3. **Observability**: Comprehensive logging at every step
4. **Resilience**: Graceful degradation when optional features unavailable
5. **Efficiency**: Shallow clones, smart caching
6. **Safety**: Dry-run mode, no remote writes

---

## User Stories

### Testing & Validation
- **Pre-deployment validation**: Test new CI/CD workflows before rolling out
- **Compliance checking**: Verify all repos pass standardized checks
- **Migration testing**: Validate workflow compatibility before platform changes

### Security & Auditing
- **Security scanning**: Run vulnerability scans across entire portfolio
- **Dependency auditing**: Check for outdated or vulnerable dependencies
- **License compliance**: Verify license compatibility across all projects

### Quality Enforcement
- **Code quality**: Batch-run linting and formatting checks
- **Documentation**: Verify README and docs are up to date
- **Test coverage**: Ensure all repos meet minimum coverage thresholds

---

## License

This tool is designed for internal use and automation. Ensure you have appropriate access to all repositories you process.

---

## Contributing

For issues or improvements, contact the DevOps team or submit feedback through your standard channels.

---

## FAQ

**Q: Does this push changes to GitHub?**
A: No, all workflow runs are local via `act`. No commits or pushes are made.

**Q: Can I use private repositories?**
A: Yes, as long as `gh` is authenticated (`gh auth login`) and you have access.

**Q: How do I handle repos that need secrets?**
A: Use `act_args` to pass `--secret-file` or set environment variables via `--env-file`.

**Q: Can I run this in CI/CD?**
A: Yes, but ensure Docker is available and you're authenticated with `gh`.

**Q: What if a repo doesn't have the dependencies my workflow needs?**
A: The workflow will fail for that repo. Review logs to identify missing dependencies.

**Q: How much disk space do I need?**
A: Depends on repo count and size. Shallow clones minimize usage, but plan for logs directory growth.

---

## Support

For assistance:
1. Check the [Troubleshooting](#troubleshooting) section
2. Review logs in the `logs/` directory
3. Run with `--dry-run` to debug configuration
4. Contact your DevOps team
