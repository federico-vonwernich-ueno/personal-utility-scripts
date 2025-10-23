# Slack Integration Guide

This guide explains how to set up Slack notifications for any script in this repository that supports Slack integration.

## Prerequisites

```bash
pip install slack-sdk urllib3
```

## Setup

### 1. Create a Slack App

1. Go to https://api.slack.com/apps
2. Click "Create New App" → "From scratch"
3. Name your app (e.g., "CI/CD Bot") and select your workspace
4. Navigate to "OAuth & Permissions"
5. Add these Bot Token Scopes:
   - `chat:write` - Send messages
   - `chat:write.public` - Send to public channels without joining
   - `files:write` - Upload files (if needed)
   - `channels:read` - List channels
   - `channels:join` - Auto-join public channels (if needed)
6. Install the app to your workspace
7. Copy the "Bot User OAuth Token" (starts with `xoxb-`)

### 2. Configure Environment Variables

```bash
# Required
export SLACK_BOT_TOKEN="xoxb-your-token-here"
export SLACK_CHANNEL="C01234567"  # Channel ID or #channel-name

# Optional - Dry run mode (test without sending)
export SLACK_DRY_RUN=1
```

**For GitHub Actions**, add these as repository secrets:
- `SLACK_BOT_TOKEN`
- `SLACK_CHANNEL`

### 3. Invite Bot to Channel

In Slack, invite the bot to your channel:
```
/invite @Your Bot Name
```

For public channels, the bot can usually auto-join. For private channels, manual invitation is required.

## Usage

Once configured, Slack notifications are sent automatically when you run scripts. Most scripts support:

- **Start notifications**: Announces when a task begins
- **Progress updates**: Real-time updates (often in threads)
- **Summary notifications**: Final results with statistics
- **File attachments**: Logs, reports, and data files

## Notification Structure

Most scripts use threaded notifications:
- **Main message**: Initial announcement with overview
- **Thread replies**: Progress updates for each step
- **Final reply**: Comprehensive summary with statistics

## Troubleshooting

### "SLACK_BOT_TOKEN not set"
Set the environment variable or suppress with `SLACK_DRY_RUN=1`

### "SLACK_CHANNEL not set"
Set the channel ID (preferred) or name with `#` prefix

### "slack-sdk not installed"
```bash
pip install slack-sdk urllib3
```

### "Bot not in channel"
For public channels, the bot should auto-join. For private channels:
```
/invite @Your Bot Name
```

### Notifications not appearing
1. Verify token is valid and not expired
2. Check channel ID is correct (use Channel ID, not name)
3. Ensure bot has `chat:write` permission
4. Run with `--verbose` flag (if supported) to see detailed errors

### Test Without Sending
Use dry-run mode to test configuration:
```bash
export SLACK_DRY_RUN=1
# Run your script
```

## Disabling Slack Notifications

Notifications are automatically disabled if environment variables are not set:

```bash
unset SLACK_BOT_TOKEN
unset SLACK_CHANNEL
```

The scripts will run normally without Slack integration.

## Security Best Practices

- Never commit tokens to version control
- Store tokens in secrets managers (GitHub Secrets, Vault, etc.)
- Use separate tokens for different environments
- Rotate tokens regularly
- Limit token scopes to minimum required permissions

## Template Customization

Some scripts support custom Slack message templates. Templates are usually located in:
- `slack-notifier/templates/` (shared templates)
- `{script-folder}/templates/` (script-specific templates)

Refer to individual script documentation for template customization options.

## Related Scripts

Scripts that support Slack integration:
- `analyze-org-repos` - Repository analysis notifications
- `ghact-runner` - Workflow execution reports
- `nullplatform-setup` - Resource creation updates
- `repository-mirrorer` - Sync progress and results
- `slack-notifier` - General-purpose notification SDK
