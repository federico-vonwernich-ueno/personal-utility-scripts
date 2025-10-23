# ghact-runner

Batch-test GitHub Actions workflows across multiple repositories locally using [nektos/act](https://github.com/nektos/act).

## Overview

`ghact-runner` clones multiple GitHub repositories, injects a standardized workflow, and runs it locally in Docker containers without committing/pushing to GitHub. Ideal for pre-deployment validation, security auditing, and testing workflow changes across an organization.

### Key Features

- Batch repository processing (dozens or hundreds of repos)
- Flexible repo formats (`org/repo`, HTTPS, SSH URLs)
- Local execution with `act` (no remote pushes)
- Smart updates (existing repos updated atomically)
- Comprehensive logging (zipped for analysis)
- Slack notifications with detailed reports
- Dry-run mode and error handling options

## Quick Start

### Prerequisites

- Python 3.8+
- GitHub CLI (`gh`) - [Setup guide](../docs/GITHUB_SETUP.md)
- Git
- Docker (required by `act`)
- [act](https://github.com/nektos/act#installation)
- PyYAML: `pip install pyyaml`

Optional for Slack notifications:
- `slack-sdk`: `pip install slack-sdk`
- See [Slack Integration Guide](../docs/SLACK_INTEGRATION.md)

### Installation

```bash
# Install act
curl https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash

# Authenticate with GitHub
gh auth login

# Install Python dependencies
pip install pyyaml slack-sdk
```

See [Python Setup Guide](../docs/PYTHON_SETUP.md) for virtual environment setup.

## Configuration

Create `repos.yml`:

```yaml
# Directory for cloned repositories
checkout_dir: ~/ghact-repos

# Repositories to process
repos:
  - owner/repo-name
  - https://github.com/org/another-repo

  # Advanced: custom name and branch
  - url: owner/special-repo
    name: custom-dir-name
    branch: develop

# Workflow to inject (Method 1: external file)
workflow_file: ./my-workflow.yml

# OR Method 2: inline YAML
workflow_inline: |
  name: Local CI Check
  on: [push]
  jobs:
    test:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - name: Run tests
          run: npm test

# Workflow filename (placed in .github/workflows/)
workflow_filename: local-ci.yml

# Act configuration
act_event: push  # Event to trigger

# Optional: platform mappings
platform_mappings:
  ubuntu-latest: catthehacker/ubuntu:act-latest

# Optional: extra act arguments
act_args:
  - --verbose

# Error handling
continue_on_error: true  # Continue on failure vs stop
```

See `repos.example.yml` for detailed examples.

## Usage

### Basic Usage

```bash
python ghact-runner.py --config repos.yml
```

### Dry-Run Mode

```bash
python ghact-runner.py --config repos.yml --dry-run
```

### Custom Tool Paths

```bash
python ghact-runner.py --config repos.yml \
  --gh-path /usr/local/bin/gh \
  --act-path /opt/bin/act
```

### With Slack Notifications

```bash
export SLACK_BOT_TOKEN="xoxb-your-token"
export SLACK_CHANNEL="#ci-notifications"
python ghact-runner.py --config repos.yml
```

See [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) for setup.

## How It Works

### Execution Flow

1. Load and validate configuration
2. Verify tools (`gh`, `git`, `docker`, `act`)
3. Clear logs directory
4. Send start notification (if Slack configured)
5. For each repository:
   - Create timestamped log file
   - Clone or update repository
   - Inject workflow into `.github/workflows/`
   - Run `act` to execute workflow locally
   - Track result (success/failure)
6. Generate summary with statistics
7. Send end notification with logs (if Slack configured)

### Repository Update Strategy

**New repositories**: Shallow clone with `--depth 1` for efficiency

**Existing repositories**:
- Creates temporary directory
- Clones fresh copy into temp
- Atomically swaps directories (prevents corruption)
- No merge conflicts or dirty working trees

### Logging

Each repository gets a dedicated log file:
```
logs/
├── repo-name-1/
│   └── 20251023-162230.log
├── repo-name-2/
│   └── 20251023-162245.log
```

All logs include:
- Clone/update output
- Workflow injection confirmation
- Complete `act` execution output
- Final success/failure status

## Example Use Cases

### Security Audit
```yaml
workflow_inline: |
  name: Security Scan
  on: [push]
  jobs:
    security:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - name: Run Trivy
          run: docker run --rm -v $(pwd):/scan aquasec/trivy:latest fs /scan
```

### Lint All Repos
```yaml
workflow_file: ./lint-workflow.yml
act_args:
  - --pull=false  # Don't pull images if cached
continue_on_error: true
```

### Test Specific Branches
```yaml
repos:
  - url: myorg/project-a
    branch: feature/new-ci
  - url: myorg/project-b
    branch: develop
continue_on_error: false  # Stop on first failure
```

## Common Issues

### "gh: command not found"
Install GitHub CLI: https://cli.github.com/ or use `--gh-path`

### "gh authentication required"
```bash
gh auth login
```

### "act: command not found"
Install act: https://github.com/nektos/act#installation or use `--act-path`

### "Docker daemon not running"
```bash
sudo systemctl start docker
sudo usermod -aG docker $USER  # Then logout/login
```

### Act workflows fail with permission errors
- Check workflow doesn't require `GITHUB_TOKEN` for write operations
- Add secrets via act's `--secret-file` option in `act_args`

For more troubleshooting, see [Contributing Guide](../CONTRIBUTING.md).

## Key Architecture Components

**Configuration**: `Config` dataclass with repos, workflow source, act settings

**Repository Handling**:
- `clone_with_gh()` - Fresh clone via GitHub CLI
- `update_existing_repo_with_gh()` - Atomic update strategy

**Workflow Management**: `write_workflow()` - Inject workflow into `.github/workflows/`

**Execution**:
- `run_act()` - Execute workflow with act
- `RunLogger` - Dual output (console + file)

**Notifications**: `send_slack_notification()` with start/progress/summary messages

## FAQ

**Q: Does this push changes to GitHub?**
A: No, all workflow runs are local via `act`. No commits or pushes.

**Q: Can I use private repositories?**
A: Yes, with `gh auth login` and proper access.

**Q: How do I handle repos that need secrets?**
A: Use `act_args` to pass `--secret-file` or `--env-file`.

**Q: Can I run this in CI/CD?**
A: Yes, ensure Docker is available and `gh` is authenticated.

## Related Documentation

- [Python Setup Guide](../docs/PYTHON_SETUP.md) - Python environment setup
- [GitHub Setup Guide](../docs/GITHUB_SETUP.md) - GitHub authentication
- [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) - Slack notifications
- [Contributing Guide](../CONTRIBUTING.md) - Common patterns and troubleshooting

## Support

For issues, review logs in the `logs/` directory, run with `--dry-run`, or check the [Contributing Guide](../CONTRIBUTING.md).
