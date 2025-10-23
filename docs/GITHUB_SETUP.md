# GitHub Setup Guide

This guide covers GitHub authentication setup for scripts in this repository.

## GitHub CLI (`gh`)

Most scripts use the GitHub CLI for authentication and API access.

### Installation

**macOS**:
```bash
brew install gh
```

**Ubuntu/Debian**:
```bash
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt update
sudo apt install gh
```

**Other platforms**: See https://cli.github.com/manual/installation

### Authentication

Authenticate with GitHub:
```bash
gh auth login
```

Follow the prompts to authenticate via web browser or token.

Verify authentication:
```bash
gh auth status
```

## Personal Access Tokens (PAT)

Some scripts require a GitHub Personal Access Token for API access.

### Creating a Token

1. Go to GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Give your token a descriptive name
4. Select required scopes (see script-specific requirements below)
5. Generate and save the token securely

### Common Token Scopes

| Script | Required Scopes | Purpose |
|--------|----------------|---------|
| **analyze-org-repos** | `repo:read`, `org:read` | Read repository and organization data |
| **repository-mirrorer** | `repo`, `admin:org` | Clone and create repositories |
| **ghact-runner** | `repo` (if using private repos) | Clone repositories |
| **workflow-monitor** | Via `gh` CLI | Read workflow runs |

### Using Tokens

**Environment variable** (recommended):
```bash
export GITHUB_TOKEN="ghp_your_token_here"
```

**Command-line argument**:
```bash
script.py --token ghp_your_token_here
```

**For GitHub Actions**, add as repository secret named `GITHUB_TOKEN` or custom name.

## API Rate Limits

GitHub API has rate limits:
- **Authenticated**: 5,000 requests/hour
- **Unauthenticated**: 60 requests/hour

Always authenticate to get higher limits. Scripts with rate limit handling will automatically:
- Throttle requests when approaching limits
- Wait and retry when limits are hit
- Display remaining quota in verbose mode

Check your current rate limit:
```bash
gh api rate_limit
```

## Troubleshooting

### "gh: command not found"
Install GitHub CLI (see Installation section above)

### "gh authentication required"
Run `gh auth login` to authenticate

### "Resource not accessible by integration"
Your token may lack required scopes. Regenerate with correct permissions.

### "API rate limit exceeded"
Wait for rate limit to reset (typically 1 hour) or:
- Reduce polling frequency
- Decrease number of API calls
- Use authenticated requests

### "Repository not found"
Verify:
- Repository name is correct (format: `owner/repo`)
- Token has access to the repository
- Repository exists and hasn't been renamed/deleted

## Security Best Practices

1. **Never commit tokens** to version control
2. **Use environment variables** for tokens
3. **Rotate tokens regularly** for security
4. **Use separate tokens** for different environments (dev, staging, prod)
5. **Limit token scopes** to minimum required permissions
6. **Use fine-grained PATs** when possible (better security)
7. **Store tokens securely** in secrets managers (GitHub Secrets, Vault, etc.)

## Organization Access

For organization repositories:
1. Your token must be authorized for the organization
2. Go to GitHub Settings → Applications → Personal access tokens
3. Find your token and click "Configure SSO"
4. Authorize for required organizations

## Related Documentation

- [GitHub CLI Manual](https://cli.github.com/manual/)
- [GitHub API Documentation](https://docs.github.com/en/rest)
- [Personal Access Token Guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token)
- [Fine-grained PATs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)
