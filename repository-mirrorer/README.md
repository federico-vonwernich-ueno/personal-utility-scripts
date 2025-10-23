# Repository Sync Script

Mirror and sync repositories from a source GitHub organization to multiple target organizations with incremental updates and conflict detection.

## Overview

`repo-sync.py` creates exact mirrors of repositories including all branches, tags, and commit history, syncing them to one or more target organizations. Supports initial migration and incremental updates with fast-forward checks.

### Key Features

- Mirror-based syncing (all branches, tags, commit history)
- Multiple target organizations simultaneously
- Incremental updates with fast-forward checks
- Conflict detection (skips diverged repositories)
- Metadata synchronization (description, topics, homepage, visibility)
- GitHub Actions ready (PAT authentication)
- Dry-run mode and detailed logging
- Slack notifications for progress and results

## Quick Start

### Prerequisites

- Python 3.8+
- Git
- GitHub Personal Access Token with scopes:
  - `repo` - Full control of repositories
  - `admin:org` - Create repos in target organizations

Create token: GitHub Settings → Developer settings → Personal access tokens → Generate new token

See [GitHub Setup Guide](../docs/GITHUB_SETUP.md) for detailed instructions.

### Installation

```bash
# Install Python dependencies
pip install -r requirements.txt
```

See [Python Setup Guide](../docs/PYTHON_SETUP.md) for virtual environment setup.

## Configuration

Create `repo-sync.yaml`:

```yaml
# Source organization (where repositories will be cloned from)
source_org: "my-source-org"

# Target organizations (where repositories will be mirrored to)
target_orgs:
  - "target-org-1"
  - "target-org-2"

# List of repositories to sync
repositories:
  - "repo-name-1"
  - "repo-name-2"
  - "my-important-project"
```

See `repo-sync.example.yaml` for more options.

## Usage

### Set GitHub Token

```bash
export GITHUB_TOKEN="ghp_your_token_here"
```

### Basic Usage

```bash
# Default config file (repo-sync.yaml)
python repo-sync.py

# Specific config file
python repo-sync.py --config my-sync-config.yaml
```

### Dry Run

```bash
python repo-sync.py --dry-run
```

### Verbose Logging

```bash
python repo-sync.py --verbose
```

### Provide Token via CLI

```bash
python repo-sync.py --token ghp_your_token_here
```

### With Slack Notifications

```bash
export SLACK_BOT_TOKEN="xoxb-your-token"
export SLACK_CHANNEL="#repo-sync"
python repo-sync.py
```

See [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) for setup.

## How It Works

### For New Repositories

1. Fetches metadata from source (description, topics, etc.)
2. Creates new repository in target organization
3. Creates mirror clone from source
4. Pushes all branches, tags, and commits to target
5. Updates repository metadata

### For Existing Repositories

1. Creates mirror clone from source
2. Fetches from target repository
3. Checks if target can be fast-forwarded to match source
   - **If yes**: Pushes updates and syncs metadata
   - **If no**: Skips (logs warning about divergence)

### What Gets Synced

✅ All branches, tags, commits
✅ Repository description, topics, homepage
✅ Default branch, visibility settings

❌ Not synced: Issues, PRs, secrets, webhooks, branch protection rules

## Example Output

```
2025-10-23 10:30:00 - INFO - Loading configuration from repo-sync.yaml
2025-10-23 10:30:00 - INFO - Config loaded: 3 repos, 2 target orgs
2025-10-23 10:30:01 - INFO - Starting sync: 3 repositories to 2 target organizations (6 total operations)
2025-10-23 10:30:01 - INFO - [1/6] Processing my-repo -> target-org-1
2025-10-23 10:30:05 - INFO - ✓ Created: target-org-1/my-repo
2025-10-23 10:30:05 - INFO - [2/6] Processing my-repo -> target-org-2
2025-10-23 10:30:10 - INFO - ✓ Updated: target-org-2/my-repo

============================================================
SYNC SUMMARY
============================================================
Total operations: 6
Created:          3
Updated:          2
Skipped:          1
Errors:           0
============================================================

Skipped repositories:
  - target-org-1/old-repo: Target has diverged from source (cannot fast-forward)
```

## Common Issues

### "Failed to mirror clone"
- Check repository exists in source org
- Verify token has access to source organization
- Check network connectivity

### "Failed to create repository in target org"
- Verify token has `admin:org` scope
- Check permission to create repos in target org
- Repository might already exist (will try to update)

### "Cannot fast-forward"
Target repository has commits not in source. Options:
1. Manually reconcile differences
2. Delete target repo and let script recreate it
3. Accept that this repo will be skipped

### "Permission denied"
- Check token scopes (`repo` and `admin:org`)
- Verify token hasn't expired
- Confirm access to both source and target organizations

For more troubleshooting, see [Contributing Guide](../CONTRIBUTING.md).

## Advanced Usage

### Test First

```bash
# Always test with dry-run first
python repo-sync.py --dry-run

# Test with small dataset
# (edit config to include only a few repos)
python repo-sync.py --verbose
```

### Handling Divergence

When repos diverge (cannot fast-forward):

**Option 1 - Manual reconciliation**:
```bash
cd target-repo
git pull source-repo
# Resolve conflicts
git push
```

**Option 2 - Force re-create**:
```bash
# Delete target repo in GitHub UI
# Re-run sync script
python repo-sync.py
```

### Incremental Sync

The script is idempotent:
- Existing repos are updated (if possible)
- New repos are created
- Safe to run multiple times

## Slack Notifications

The script supports threaded Slack notifications:
- Sync start with configuration details
- Progress updates for each repository
- Summary with statistics and any errors

Setup:
1. Install dependencies: `pip install slack-sdk urllib3`
2. Configure environment variables (see [Slack Integration Guide](../docs/SLACK_INTEGRATION.md))
3. Run script normally - notifications sent automatically

## Best Practices

1. **Test with dry-run** before actual sync
2. **Start small** - test with a few repositories first
3. **Use separate tokens** for this script
4. **Monitor logs** - review summary for skipped/errored repos
5. **Handle divergence carefully** - investigate before force-syncing
6. **Rotate tokens regularly** for security

## Security Notes

- Never commit tokens to version control
- Add `repo-sync.yaml` to `.gitignore` if it contains sensitive org names
- Use GitHub Secrets for tokens in Actions workflows
- Limit token scopes to minimum required
- Regularly rotate Personal Access Tokens

## Related Documentation

- [Python Setup Guide](../docs/PYTHON_SETUP.md) - Python environment setup
- [GitHub Setup Guide](../docs/GITHUB_SETUP.md) - GitHub authentication and tokens
- [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) - Slack notifications
- [Contributing Guide](../CONTRIBUTING.md) - Common patterns and troubleshooting
