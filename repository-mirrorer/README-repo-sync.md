# Repository Sync Script

A Python script to mirror and sync repositories from a source GitHub organization to multiple target organizations. Supports initial migration and incremental updates with conflict detection.

## Features

- **Mirror-based syncing**: Creates exact mirrors of repositories including all branches, tags, and commit history
- **Multiple target organizations**: Sync to one or more target organizations simultaneously
- **Incremental updates**: For existing repositories, performs fast-forward updates when possible
- **Conflict detection**: Skips repositories that have diverged (cannot be fast-forwarded)
- **Metadata synchronization**: Syncs repository description, topics, homepage, visibility, and default branch
- **GitHub Actions ready**: Uses Personal Access Token authentication, perfect for CI/CD workflows
- **Dry-run mode**: Preview changes without actually making them
- **Detailed logging**: Track progress and troubleshoot issues

## Prerequisites

- Python 3.8 or higher
- Git installed and available in PATH
- GitHub Personal Access Token with appropriate permissions

### Required GitHub Token Permissions

Your Personal Access Token needs the following scopes:

- `repo` - Full control of private repositories (includes public repos)
- `admin:org` - Full control of organizations (needed to create repos in target orgs)

To create a token:
1. Go to GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Select the required scopes
4. Generate and save the token securely

## Installation

1. Clone or copy the script to your local machine

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Configuration

### 1. Create Configuration File

Copy the example config and customize it:

```bash
cp repo-sync.example.yaml repo-sync.yaml
```

Edit `repo-sync.yaml`:

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

### 2. Set GitHub Token

Set your GitHub Personal Access Token as an environment variable:

```bash
export GITHUB_TOKEN="ghp_your_token_here"
```

Or create a `.env` file (add to `.gitignore`):
```bash
GITHUB_TOKEN=ghp_your_token_here
```

## Usage

### Basic Usage

Sync repositories using the default config file:

```bash
python repo-sync.py
```

### Specify Config File

Use a specific configuration file:

```bash
python repo-sync.py --config my-sync-config.yaml
```

### Dry Run

Preview what would happen without making changes:

```bash
python repo-sync.py --dry-run
```

### Verbose Logging

Enable detailed logging for troubleshooting:

```bash
python repo-sync.py --verbose
```

### Provide Token via CLI

Pass the token directly (not recommended for production):

```bash
python repo-sync.py --token ghp_your_token_here
```

### Combined Options

```bash
python repo-sync.py --config repo-sync.yaml --dry-run --verbose
```

## How It Works

### For New Repositories (don't exist in target org)

1. Fetches metadata from source repository (description, topics, etc.)
2. Creates new repository in target organization
3. Creates a mirror clone from source
4. Pushes all branches, tags, and commits to target
5. Updates repository metadata (description, topics, default branch, visibility)

### For Existing Repositories

1. Creates a mirror clone from source
2. Fetches from target repository
3. Checks if target can be fast-forwarded to match source
   - **If yes**: Pushes updates and syncs metadata
   - **If no**: Skips (logs warning about divergence)

### What Gets Synced

- All branches
- All tags
- All commits
- Repository description
- Repository topics
- Homepage URL
- Default branch
- Visibility settings (public/private)

### What Doesn't Get Synced

- Issues
- Pull requests
- GitHub Actions workflows (they are synced as files, but not as workflow runs)
- Secrets
- Webhooks
- Branch protection rules
- Repository settings (beyond metadata)

## GitHub Actions Integration

### Setup

1. Copy the example workflow:
```bash
cp .github/workflows/repo-sync.example.yml .github/workflows/repo-sync.yml
```

2. Create a GitHub Secret:
   - Go to your repository Settings → Secrets and variables → Actions
   - Create a new secret named `REPO_SYNC_TOKEN`
   - Paste your Personal Access Token

3. Commit your config file:
```bash
git add repo-sync.yaml
git commit -m "Add repository sync configuration"
git push
```

### Run the Workflow

The workflow can be triggered:

- **Automatically**: Runs daily at midnight UTC (configurable via cron)
- **Manually**: Go to Actions → Repository Sync → Run workflow
  - Option to enable dry-run mode
  - Option to enable verbose logging

### Workflow Features

- Automatic Python environment setup
- Dependency caching for faster runs
- Error log upload on failure
- Manual trigger with dry-run option

## Error Handling

The script handles several error scenarios:

### Skipped Repositories

Repositories are skipped when:
- Target has diverged from source (cannot fast-forward)
- Manual intervention needed

These are logged as warnings and won't fail the script.

### Errors

The script will log errors but continue processing other repositories when:
- Permission denied (check token scopes)
- Repository not found in source org
- Network errors
- Git operation failures

The script exits with code 1 if any errors occurred.

## Example Output

```
2025-10-20 10:30:00 - INFO - Loading configuration from repo-sync.yaml
2025-10-20 10:30:00 - INFO - Config loaded: 3 repos, 2 target orgs
2025-10-20 10:30:01 - INFO - Starting sync: 3 repositories to 2 target organizations (6 total operations)
2025-10-20 10:30:01 - INFO - [1/6] Processing my-repo -> target-org-1
2025-10-20 10:30:05 - INFO - ✓ Created: target-org-1/my-repo
2025-10-20 10:30:05 - INFO - [2/6] Processing my-repo -> target-org-2
2025-10-20 10:30:10 - INFO - ✓ Updated: target-org-2/my-repo
...

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

## Troubleshooting

### "Failed to mirror clone"

- Check that the repository exists in the source org
- Verify your token has access to the source organization
- Check network connectivity

### "Failed to create repository in target org"

- Verify your token has `admin:org` scope
- Check you have permission to create repos in the target org
- Repository might already exist (will skip creation and try to update)

### "Cannot fast-forward"

This is expected behavior when the target repository has commits not in the source. Options:

1. Manually reconcile the differences
2. Delete the target repo and let the script recreate it
3. Accept that this repo will be skipped

### "Permission denied"

- Check your token scopes (needs `repo` and `admin:org`)
- Verify the token hasn't expired
- Confirm you have access to both source and target organizations

## Slack Integration

The repository sync script can send real-time notifications to Slack, keeping your team informed of sync progress and results.

### Features

- **Threaded notifications**: All updates posted as threaded replies for easy tracking
- **Sync start notification**: Announces when sync begins with configuration details
- **Progress updates**: Real-time updates for each repository (created/updated/skipped/error)
- **Summary notification**: Comprehensive summary at the end with statistics
- **Rich metadata**: Includes repository descriptions, visibility, branches, and more
- **Error resilient**: Script continues even if Slack notifications fail

### Setup

#### 1. Install Slack Dependencies

```bash
pip install slack-sdk urllib3
```

#### 2. Create a Slack App

1. Go to https://api.slack.com/apps
2. Click "Create New App" → "From scratch"
3. Name it "Repository Sync Bot" and select your workspace
4. Navigate to "OAuth & Permissions"
5. Add these Bot Token Scopes:
   - `chat:write` - Send messages
   - `chat:write.public` - Send to public channels without joining
   - `files:write` - Upload files (optional)
6. Install the app to your workspace
7. Copy the "Bot User OAuth Token" (starts with `xoxb-`)

#### 3. Configure Environment Variables

Set these environment variables:

```bash
# Required for Slack notifications
export SLACK_BOT_TOKEN="xoxb-your-token-here"
export SLACK_CHANNEL="C01234567"  # Channel ID or #channel-name

# Optional
export SLACK_DRY_RUN=1  # Test without sending actual notifications
```

For GitHub Actions, add these as repository secrets:
- `SLACK_BOT_TOKEN` - Your Slack bot token
- `SLACK_CHANNEL` - Your Slack channel ID

#### 4. Invite Bot to Channel

In Slack, invite the bot to your channel:
```
/invite @Repository Sync Bot
```

### Usage

Once configured, Slack notifications are sent automatically:

```bash
# Run with Slack notifications (if env vars are set)
python repo-sync.py --config repo-sync.yaml

# Test Slack notifications without actual sync
export SLACK_DRY_RUN=1
python repo-sync.py --config repo-sync.yaml --dry-run
```

### Notification Flow

1. **Main Message**: Sync start announcement
   - Source organization
   - Target organizations list
   - Repository count
   - Total operations

2. **Thread Replies**: Progress updates for each repository
   - Status (created/updated/skipped/error)
   - Repository details
   - Metadata (description, visibility, default branch)
   - Any error messages

3. **Final Thread Reply**: Comprehensive summary
   - Statistics (created, updated, skipped, errors)
   - Duration
   - List of errors and skipped repos
   - Overall status

### Example Slack Notification

```
🔄 Repository Sync Starting

Source Organization: my-source-org
Total Operations: 6

Repositories: 3
Target Organizations: 2

Target Organizations:
• target-org-1
• target-org-2

Status: INFO | Progress updates will be posted in this thread
```

Thread replies:
```
✓ Created: my-repo → target-org-1
Source: my-source-org/my-repo
Target: target-org-1/my-repo
Visibility: Public
Default Branch: main
Description: My awesome repository

---

✓ Updated: my-repo → target-org-2
...

---

✓ Repository Sync Complete

Summary Statistics
Total Operations: 6
Duration: 2m 34s

Created: ✓ 3
Updated: 🔄 2
Skipped: ⚠️ 1
Errors: ✗ 0
```

### Troubleshooting Slack

#### "SLACK_BOT_TOKEN not set"

- Set the `SLACK_BOT_TOKEN` environment variable
- Or set `SLACK_DRY_RUN=1` to suppress the warning

#### "SLACK_CHANNEL not set"

- Set the `SLACK_CHANNEL` environment variable
- Use channel ID (C01234567) or name (#repo-sync)

#### "slack-sdk not installed"

```bash
pip install slack-sdk urllib3
```

#### "Bot not in channel"

For public channels, the bot can auto-join. For private channels:
```
/invite @Repository Sync Bot
```

#### Notifications not appearing

1. Check bot token is valid
2. Verify channel ID is correct
3. Ensure bot has `chat:write` permission
4. Check script logs for Slack errors (run with `--verbose`)

### Customizing Notifications

Templates are located in `slack-notifier/templates/`:
- `repo_sync_start.json` - Sync start message
- `repo_sync_progress.json` - Progress update per repo
- `repo_sync_summary.json` - Final summary

You can customize these templates to change the notification format. Templates use variables like `{{REPO_NAME}}`, `{{STATUS}}`, etc.

### Disabling Slack

Slack notifications are automatically disabled if environment variables are not set. To explicitly disable:

```bash
# Don't set SLACK_BOT_TOKEN or SLACK_CHANNEL
unset SLACK_BOT_TOKEN
unset SLACK_CHANNEL
```

The script will run normally without attempting Slack notifications.

## Best Practices

1. **Test with dry-run first**: Always run with `--dry-run` before actual sync
2. **Start small**: Test with a few repositories before syncing many
3. **Use separate tokens**: Create a dedicated token for this script
4. **Monitor logs**: Review the summary and check for skipped/errored repos
5. **Handle divergence carefully**: Investigate why repos diverged before force-syncing
6. **Rotate tokens regularly**: Update your PAT periodically for security

## Security Notes

- Never commit your GitHub token to version control
- Add `repo-sync.yaml` to `.gitignore` if it contains sensitive org names
- Use GitHub Secrets for tokens in Actions workflows
- Limit token scopes to minimum required permissions
- Regularly rotate your Personal Access Tokens

## License

This script is provided as-is for internal use.

## Support

For issues or questions, please contact your team administrator.
