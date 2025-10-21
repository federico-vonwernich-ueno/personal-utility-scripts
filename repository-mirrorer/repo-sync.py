#!/usr/bin/env python3
"""
Repository Sync Script

Mirrors repositories from a source organization to multiple target organizations.
Supports initial migration and incremental updates via fast-forward.
"""

import argparse
import logging
import os
import subprocess
import sys
import tempfile
import shutil
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
    from github import Github, GithubException
except ImportError:
    print("Error: Required packages not installed.")
    print("Please run: pip install PyGithub pyyaml")
    sys.exit(1)


@dataclass
class SyncResult:
    """Result of syncing a single repository to a target org"""
    repo_name: str
    target_org: str
    status: str  # 'created', 'updated', 'skipped', 'error'
    message: str


@dataclass
class Config:
    """Configuration for repository sync"""
    source_org: str
    target_orgs: List[str]
    repositories: List[str]


class RepoSyncer:
    """Handles repository mirroring and synchronization"""

    def __init__(self, token: str, dry_run: bool = False, verbose: bool = False):
        self.token = token
        self.dry_run = dry_run
        self.verbose = verbose
        self.github = Github(token)
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """Configure logging"""
        logger = logging.getLogger('repo-sync')
        level = logging.DEBUG if self.verbose else logging.INFO
        logger.setLevel(level)

        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        return logger

    def load_config(self, config_path: str) -> Config:
        """Load and validate configuration from YAML file"""
        self.logger.info(f"Loading configuration from {config_path}")

        try:
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f)
        except Exception as e:
            self.logger.error(f"Failed to load config file: {e}")
            sys.exit(1)

        # Validate required fields
        required_fields = ['source_org', 'target_orgs', 'repositories']
        for field in required_fields:
            if field not in data:
                self.logger.error(f"Missing required field in config: {field}")
                sys.exit(1)

        if not isinstance(data['target_orgs'], list):
            self.logger.error("'target_orgs' must be a list")
            sys.exit(1)

        if not isinstance(data['repositories'], list):
            self.logger.error("'repositories' must be a list")
            sys.exit(1)

        config = Config(
            source_org=data['source_org'],
            target_orgs=data['target_orgs'],
            repositories=data['repositories']
        )

        self.logger.info(f"Config loaded: {len(config.repositories)} repos, "
                        f"{len(config.target_orgs)} target orgs")

        return config

    def _run_command(self, cmd: List[str], cwd: Optional[str] = None) -> Tuple[int, str, str]:
        """Run a shell command and return (returncode, stdout, stderr)"""
        self.logger.debug(f"Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            self.logger.debug(f"Command failed with code {result.returncode}")
            self.logger.debug(f"stderr: {result.stderr}")

        return result.returncode, result.stdout, result.stderr

    def _get_auth_url(self, repo_url: str) -> str:
        """Convert repo URL to authenticated HTTPS URL"""
        # Convert git@github.com:org/repo.git to https://token@github.com/org/repo.git
        if repo_url.startswith('git@'):
            repo_url = repo_url.replace('git@github.com:', 'https://github.com/')

        # Add token authentication
        auth_url = repo_url.replace('https://', f'https://{self.token}@')

        return auth_url

    def _repo_exists(self, org: str, repo_name: str) -> bool:
        """Check if repository exists in organization"""
        try:
            self.github.get_repo(f"{org}/{repo_name}")
            return True
        except GithubException as e:
            if e.status == 404:
                return False
            raise

    def _get_repo_metadata(self, org: str, repo_name: str) -> Dict:
        """Get repository metadata"""
        try:
            repo = self.github.get_repo(f"{org}/{repo_name}")
            return {
                'description': repo.description or '',
                'homepage': repo.homepage or '',
                'topics': repo.get_topics(),
                'private': repo.private,
                'default_branch': repo.default_branch
            }
        except GithubException as e:
            self.logger.error(f"Failed to get metadata for {org}/{repo_name}: {e}")
            return {}

    def _set_repo_metadata(self, org: str, repo_name: str, metadata: Dict) -> bool:
        """Set repository metadata"""
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would update metadata for {org}/{repo_name}")
            return True

        try:
            repo = self.github.get_repo(f"{org}/{repo_name}")

            # Update basic metadata
            repo.edit(
                description=metadata.get('description', ''),
                homepage=metadata.get('homepage', ''),
                private=metadata.get('private', False)
            )

            # Update topics
            if 'topics' in metadata and metadata['topics']:
                repo.replace_topics(metadata['topics'])

            # Update default branch (if different)
            if 'default_branch' in metadata:
                current_default = repo.default_branch
                new_default = metadata['default_branch']
                if current_default != new_default:
                    # Check if the branch exists in target repo
                    try:
                        repo.get_branch(new_default)
                        repo.edit(default_branch=new_default)
                        self.logger.debug(f"Updated default branch to {new_default}")
                    except GithubException:
                        self.logger.warning(f"Branch {new_default} doesn't exist in target, "
                                          f"keeping default as {current_default}")

            return True
        except GithubException as e:
            self.logger.error(f"Failed to set metadata for {org}/{repo_name}: {e}")
            return False

    def _create_repo(self, org: str, repo_name: str, metadata: Dict) -> bool:
        """Create a new repository in the target organization"""
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would create repository {org}/{repo_name}")
            return True

        try:
            org_obj = self.github.get_organization(org)
            org_obj.create_repo(
                name=repo_name,
                description=metadata.get('description', ''),
                homepage=metadata.get('homepage', ''),
                private=metadata.get('private', False),
                auto_init=False  # Don't create initial commit
            )

            self.logger.info(f"Created repository {org}/{repo_name}")

            # Set topics (can't be set during creation)
            if 'topics' in metadata and metadata['topics']:
                repo = self.github.get_repo(f"{org}/{repo_name}")
                repo.replace_topics(metadata['topics'])

            return True
        except GithubException as e:
            self.logger.error(f"Failed to create repository {org}/{repo_name}: {e}")
            return False

    def _mirror_clone(self, source_org: str, repo_name: str, temp_dir: str, default_branch: str) -> bool:
        """Clone repository default branch and tags"""
        source_url = f"https://github.com/{source_org}/{repo_name}.git"
        auth_url = self._get_auth_url(source_url)

        mirror_path = os.path.join(temp_dir, f"{repo_name}.git")

        self.logger.debug(f"Cloning {source_org}/{repo_name} (branch: {default_branch})")

        # Clone only the default branch as bare repo
        returncode, stdout, stderr = self._run_command([
            'git', 'clone', '--bare', '--single-branch', '--branch', default_branch, auth_url, mirror_path
        ])

        if returncode != 0:
            self.logger.error(f"Failed to clone {source_org}/{repo_name}")
            return False

        # Fetch all tags
        self.logger.debug(f"Fetching tags for {source_org}/{repo_name}")
        returncode, stdout, stderr = self._run_command([
            'git', 'fetch', 'origin', 'refs/tags/*:refs/tags/*'
        ], cwd=mirror_path)

        if returncode != 0:
            self.logger.warning(f"Failed to fetch tags for {source_org}/{repo_name}: {stderr}")
            # Don't fail the whole operation if tags fail

        return True

    def _push_mirror(self, repo_name: str, temp_dir: str, target_org: str, default_branch: str) -> bool:
        """Push default branch and tags to target organization"""
        mirror_path = os.path.join(temp_dir, f"{repo_name}.git")
        target_url = f"https://github.com/{target_org}/{repo_name}.git"
        auth_url = self._get_auth_url(target_url)

        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would push {default_branch} and tags to {target_org}/{repo_name}")
            return True

        # Push default branch
        self.logger.debug(f"Pushing {default_branch} to {target_org}/{repo_name}")
        returncode, stdout, stderr = self._run_command([
            'git', 'push', auth_url, default_branch
        ], cwd=mirror_path)

        if returncode != 0:
            self.logger.error(f"Failed to push {default_branch} to {target_org}/{repo_name}")
            self.logger.error(f"Error: {stderr}")
            return False

        # Push tags
        self.logger.debug(f"Pushing tags to {target_org}/{repo_name}")
        returncode, stdout, stderr = self._run_command([
            'git', 'push', auth_url, 'refs/tags/*:refs/tags/*'
        ], cwd=mirror_path)

        if returncode != 0:
            self.logger.warning(f"Failed to push tags to {target_org}/{repo_name}: {stderr}")
            # Don't fail the whole operation if tags fail

        return True

    def _can_fast_forward(self, repo_name: str, temp_dir: str,
                         source_org: str, target_org: str) -> Tuple[bool, str]:
        """
        Check if target repo can be fast-forwarded to match source.
        Returns (can_ff, message)
        """
        mirror_path = os.path.join(temp_dir, f"{repo_name}.git")

        # Fetch from target
        target_url = f"https://github.com/{target_org}/{repo_name}.git"
        auth_url = self._get_auth_url(target_url)

        returncode, stdout, stderr = self._run_command([
            'git', 'remote', 'add', 'target', auth_url
        ], cwd=mirror_path)

        if returncode != 0:
            return False, f"Failed to add target remote: {stderr}"

        returncode, stdout, stderr = self._run_command([
            'git', 'fetch', 'target'
        ], cwd=mirror_path)

        if returncode != 0:
            return False, f"Failed to fetch from target: {stderr}"

        # Get default branch from source metadata
        source_repo = self.github.get_repo(f"{source_org}/{repo_name}")
        default_branch = source_repo.default_branch

        # Check if target's default branch is ancestor of source's default branch
        returncode, stdout, stderr = self._run_command([
            'git', 'merge-base', '--is-ancestor',
            f'target/{default_branch}', f'origin/{default_branch}'
        ], cwd=mirror_path)

        if returncode == 0:
            return True, "Can fast-forward"
        elif returncode == 1:
            return False, "Target has diverged from source (cannot fast-forward)"
        else:
            return False, f"Failed to check ancestry: {stderr}"

    def sync_repository(self, source_org: str, repo_name: str,
                       target_org: str) -> SyncResult:
        """
        Sync a single repository from source to target organization.
        Returns SyncResult with status and message.
        """
        self.logger.info(f"Syncing {repo_name}: {source_org} -> {target_org}")

        # Get source metadata
        source_metadata = self._get_repo_metadata(source_org, repo_name)
        if not source_metadata:
            return SyncResult(
                repo_name=repo_name,
                target_org=target_org,
                status='error',
                message='Failed to get source metadata'
            )

        # Extract default branch
        default_branch = source_metadata.get('default_branch')
        if not default_branch:
            return SyncResult(
                repo_name=repo_name,
                target_org=target_org,
                status='error',
                message='Failed to get default branch from source metadata'
            )

        # Check if target repo exists
        target_exists = self._repo_exists(target_org, repo_name)

        # Create temporary directory for git operations
        temp_dir = tempfile.mkdtemp(prefix=f'repo-sync-{repo_name}-')

        try:
            # Clone default branch and tags from source
            if not self._mirror_clone(source_org, repo_name, temp_dir, default_branch):
                return SyncResult(
                    repo_name=repo_name,
                    target_org=target_org,
                    status='error',
                    message='Failed to mirror clone from source'
                )

            if not target_exists:
                # Create new repository in target org
                if not self._create_repo(target_org, repo_name, source_metadata):
                    return SyncResult(
                        repo_name=repo_name,
                        target_org=target_org,
                        status='error',
                        message='Failed to create repository in target org'
                    )

                # Push default branch and tags to target
                if not self._push_mirror(repo_name, temp_dir, target_org, default_branch):
                    return SyncResult(
                        repo_name=repo_name,
                        target_org=target_org,
                        status='error',
                        message='Failed to push to target'
                    )

                # Update metadata
                self._set_repo_metadata(target_org, repo_name, source_metadata)

                return SyncResult(
                    repo_name=repo_name,
                    target_org=target_org,
                    status='created',
                    message='Repository created and mirrored successfully'
                )

            else:
                # Repository exists - check if we can fast-forward
                can_ff, ff_message = self._can_fast_forward(
                    repo_name, temp_dir, source_org, target_org
                )

                if can_ff:
                    # Push updates
                    if not self._push_mirror(repo_name, temp_dir, target_org, default_branch):
                        return SyncResult(
                            repo_name=repo_name,
                            target_org=target_org,
                            status='error',
                            message='Failed to push updates to target'
                        )

                    # Update metadata
                    self._set_repo_metadata(target_org, repo_name, source_metadata)

                    return SyncResult(
                        repo_name=repo_name,
                        target_org=target_org,
                        status='updated',
                        message='Repository updated successfully'
                    )
                else:
                    # Cannot fast-forward - skip
                    return SyncResult(
                        repo_name=repo_name,
                        target_org=target_org,
                        status='skipped',
                        message=f'Skipped: {ff_message}'
                    )

        except Exception as e:
            self.logger.exception(f"Unexpected error syncing {repo_name}")
            return SyncResult(
                repo_name=repo_name,
                target_org=target_org,
                status='error',
                message=f'Unexpected error: {str(e)}'
            )

        finally:
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                self.logger.warning(f"Failed to clean up temp directory {temp_dir}: {e}")

    def sync_all(self, config: Config) -> List[SyncResult]:
        """
        Sync all repositories from config to all target organizations.
        Returns list of SyncResult objects.
        """
        import time

        results = []
        start_time = time.time()

        total_syncs = len(config.repositories) * len(config.target_orgs)
        current = 0

        self.logger.info(f"Starting sync: {len(config.repositories)} repositories "
                        f"to {len(config.target_orgs)} target organizations "
                        f"({total_syncs} total operations)")

        # Send Slack start notification
        thread_ts = None
        try:
            slack_rc, thread_ts = send_sync_start_notification(config)
            if slack_rc == 0:
                self.logger.debug("[SLACK] Sync start notification sent successfully")
            elif slack_rc not in (2, 3, 4):  # Ignore missing deps/config errors
                self.logger.debug(f"[SLACK] Start notification failed with code {slack_rc}")
        except Exception as e:
            self.logger.debug(f"[SLACK] Failed to send start notification: {e}")

        for repo_name in config.repositories:
            for target_org in config.target_orgs:
                current += 1
                self.logger.info(f"[{current}/{total_syncs}] Processing {repo_name} -> {target_org}")

                result = self.sync_repository(config.source_org, repo_name, target_org)
                results.append(result)

                # Log result
                if result.status == 'created':
                    self.logger.info(f"✓ Created: {target_org}/{repo_name}")
                elif result.status == 'updated':
                    self.logger.info(f"✓ Updated: {target_org}/{repo_name}")
                elif result.status == 'skipped':
                    self.logger.warning(f"⊘ Skipped: {target_org}/{repo_name} - {result.message}")
                elif result.status == 'error':
                    self.logger.error(f"✗ Error: {target_org}/{repo_name} - {result.message}")

                # Send Slack progress notification (threaded)
                try:
                    # Get metadata for the notification
                    metadata = None
                    if result.status in ('created', 'updated'):
                        try:
                            metadata = self._get_repo_metadata(target_org, repo_name)
                        except Exception:
                            pass  # Metadata is optional for notifications

                    slack_rc = send_repo_sync_notification(
                        result,
                        config.source_org,
                        metadata=metadata,
                        thread_ts=thread_ts
                    )
                    if slack_rc == 0:
                        self.logger.debug(f"[SLACK] Progress notification sent for {repo_name}")
                    elif slack_rc not in (2, 3, 4):
                        self.logger.debug(f"[SLACK] Progress notification failed with code {slack_rc}")
                except Exception as e:
                    self.logger.debug(f"[SLACK] Failed to send progress notification: {e}")

        # Calculate duration
        duration = time.time() - start_time

        # Send Slack summary notification (threaded)
        try:
            slack_rc = send_sync_summary_notification(
                config,
                results,
                duration_seconds=duration,
                thread_ts=thread_ts
            )
            if slack_rc == 0:
                self.logger.debug("[SLACK] Summary notification sent successfully")
            elif slack_rc not in (2, 3, 4):
                self.logger.debug(f"[SLACK] Summary notification failed with code {slack_rc}")
        except Exception as e:
            self.logger.debug(f"[SLACK] Failed to send summary notification: {e}")

        return results

    def print_summary(self, results: List[SyncResult]):
        """Print summary of sync results"""
        total = len(results)
        created = sum(1 for r in results if r.status == 'created')
        updated = sum(1 for r in results if r.status == 'updated')
        skipped = sum(1 for r in results if r.status == 'skipped')
        errors = sum(1 for r in results if r.status == 'error')

        print("\n" + "="*60)
        print("SYNC SUMMARY")
        print("="*60)
        print(f"Total operations: {total}")
        print(f"Created:          {created}")
        print(f"Updated:          {updated}")
        print(f"Skipped:          {skipped}")
        print(f"Errors:           {errors}")
        print("="*60)

        if errors > 0:
            print("\nErrors encountered:")
            for result in results:
                if result.status == 'error':
                    print(f"  - {result.target_org}/{result.repo_name}: {result.message}")

        if skipped > 0:
            print("\nSkipped repositories:")
            for result in results:
                if result.status == 'skipped':
                    print(f"  - {result.target_org}/{result.repo_name}: {result.message}")


# ============================================================================
# SLACK NOTIFICATIONS
# ============================================================================

def validate_slack_config() -> Optional[int]:
    """
    Validate Slack environment configuration.

    Returns:
        None if valid, or exit code if invalid/missing config
    """
    dry_run_flag = os.environ.get("SLACK_DRY_RUN")
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL")

    if not token and not dry_run_flag:
        return 3  # SLACK_NO_TOKEN

    if not channel and not dry_run_flag:
        return 4  # SLACK_NO_CHANNEL

    return None


def check_slack_dependencies() -> Optional[int]:
    """
    Check if Slack SDK dependencies are available.

    Returns:
        None if dependencies available, or exit code if missing
    """
    try:
        import importlib
        importlib.import_module('slack_sdk')
        importlib.import_module('urllib3')
        return None
    except Exception:
        dry_run_flag = os.environ.get("SLACK_DRY_RUN")

        if dry_run_flag:
            return None  # Allow dry-run to proceed
        else:
            return 2  # MISSING_DEPENDENCY


def send_slack_notification(
    title: str,
    message: str = "",
    status: str = "info",
    template: Optional[str] = None,
    template_vars: Optional[Dict[str, str]] = None,
    thread_ts: Optional[str] = None
) -> Tuple[int, Optional[str]]:
    """
    Send notification via Slack using companion notifier script.

    Respects SLACK_DRY_RUN, SLACK_BOT_TOKEN, SLACK_CHANNEL env vars.

    Args:
        title: Notification title
        message: Message body
        status: Status type (info, success, failure, warning)
        template: Template name (optional)
        template_vars: Template variables (optional)
        thread_ts: Thread timestamp for threaded replies (optional)

    Returns:
        Tuple of (exit_code, thread_ts) where thread_ts is returned for new threads
    """
    # Locate notifier script
    script_dir = Path(__file__).parent
    slack_script = (script_dir / "slack-notifier" / "slack_notifier_sdk.py").resolve()

    # Validate configuration
    config_error = validate_slack_config()
    if config_error:
        return config_error, None

    if not slack_script.exists():
        return 2, None  # MISSING_DEPENDENCY

    # Check dependencies
    dep_error = check_slack_dependencies()
    if dep_error:
        return dep_error, None

    # Build command
    dry_run_flag = bool(os.environ.get("SLACK_DRY_RUN"))
    cmd = [sys.executable, str(slack_script), "--title", title, "--status", status]

    if message:
        cmd.extend(["--message", message])

    if dry_run_flag:
        cmd.append("--dry-run")

    if template:
        cmd.extend(["--template", template])

    if template_vars:
        for k, v in template_vars.items():
            if k is not None and v is not None:
                cmd.extend(["--var", f"{k}={v}"])

    # Note: thread_ts support would require modifications to slack_notifier_sdk.py
    # For now, we'll track it internally but won't pass it to the notifier

    # Execute
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        # Return thread_ts as None for now (would need notifier API changes to get actual ts)
        return result.returncode, None
    except Exception:
        return 1, None


def send_sync_start_notification(config: Config) -> Tuple[int, Optional[str]]:
    """
    Send initial Slack notification when sync starts.

    Args:
        config: Configuration object

    Returns:
        Tuple of (exit_code, thread_ts)
    """
    title = "Repository Sync Starting"

    target_orgs_list = "\n".join([f"• {org}" for org in config.target_orgs])
    repo_list = "\n".join([f"• {repo}" for repo in config.repositories[:10]])
    if len(config.repositories) > 10:
        repo_list += f"\n• ... and {len(config.repositories) - 10} more"

    total_operations = len(config.repositories) * len(config.target_orgs)

    message = (
        f"*Source Organization:* {config.source_org}\n\n"
        f"*Target Organizations ({len(config.target_orgs)}):*\n{target_orgs_list}\n\n"
        f"*Repositories ({len(config.repositories)}):*\n{repo_list}\n\n"
        f"*Total Operations:* {total_operations}\n"
    )

    return send_slack_notification(
        title,
        message,
        status="info",
        template="repo_sync_start",
        template_vars={
            "SOURCE_ORG": config.source_org,
            "TARGET_ORGS_LIST": target_orgs_list,
            "REPO_COUNT": str(len(config.repositories)),
            "TARGET_ORG_COUNT": str(len(config.target_orgs)),
            "TOTAL_OPS": str(total_operations)
        }
    )


def send_repo_sync_notification(
    result: SyncResult,
    source_org: str,
    metadata: Optional[Dict] = None,
    thread_ts: Optional[str] = None
) -> int:
    """
    Send Slack notification for a single repository sync result.

    Args:
        result: SyncResult object
        source_org: Source organization name
        metadata: Repository metadata dictionary (optional)
        thread_ts: Thread timestamp for threading (optional)

    Returns:
        Exit code
    """
    # Map status to notification status and icon
    status_map = {
        'created': ('success', ':white_check_mark:', 'Created'),
        'updated': ('success', ':arrows_counterclockwise:', 'Updated'),
        'skipped': ('warning', ':warning:', 'Skipped'),
        'error': ('failure', ':x:', 'Error')
    }

    slack_status, icon, action = status_map.get(result.status, ('info', ':speech_balloon:', 'Processed'))

    title = f"{icon} {action}: {result.repo_name} → {result.target_org}"

    # Build message with metadata
    message_parts = [
        f"*Repository:* {source_org}/{result.repo_name}",
        f"*Target:* {result.target_org}/{result.repo_name}",
        f"*Status:* {action}",
    ]

    if metadata:
        if metadata.get('description'):
            message_parts.append(f"*Description:* {metadata['description']}")
        if 'private' in metadata:
            visibility = 'Private' if metadata['private'] else 'Public'
            message_parts.append(f"*Visibility:* {visibility}")
        if metadata.get('default_branch'):
            message_parts.append(f"*Default Branch:* {metadata['default_branch']}")

    if result.message:
        message_parts.append(f"\n_{result.message}_")

    message = "\n".join(message_parts)

    exit_code, _ = send_slack_notification(
        title,
        message,
        status=slack_status,
        template="repo_sync_progress",
        template_vars={
            "REPO_NAME": result.repo_name,
            "TARGET_ORG": result.target_org,
            "SOURCE_ORG": source_org,
            "STATUS": action,
            "STATUS_ICON": icon,
            "DESCRIPTION": metadata.get('description', '') if metadata else '',
            "VISIBILITY": ('Private' if metadata and metadata.get('private') else 'Public') if metadata else '',
            "DEFAULT_BRANCH": metadata.get('default_branch', '') if metadata else '',
            "MESSAGE": result.message
        },
        thread_ts=thread_ts
    )

    return exit_code


def send_sync_summary_notification(
    config: Config,
    results: List[SyncResult],
    duration_seconds: Optional[float] = None,
    thread_ts: Optional[str] = None
) -> int:
    """
    Send Slack notification with sync summary.

    Args:
        config: Configuration object
        results: List of SyncResult objects
        duration_seconds: Duration of sync in seconds (optional)
        thread_ts: Thread timestamp for threading (optional)

    Returns:
        Exit code
    """
    total = len(results)
    created = sum(1 for r in results if r.status == 'created')
    updated = sum(1 for r in results if r.status == 'updated')
    skipped = sum(1 for r in results if r.status == 'skipped')
    errors = sum(1 for r in results if r.status == 'error')

    # Determine overall status
    if errors > 0:
        overall_status = 'failure'
        title = ":x: Repository Sync Completed with Errors"
    elif skipped > 0:
        overall_status = 'warning'
        title = ":warning: Repository Sync Completed with Skipped Repos"
    else:
        overall_status = 'success'
        title = ":white_check_mark: Repository Sync Completed Successfully"

    # Build summary message
    message_parts = [
        "*Summary:*",
        f"• Total operations: {total}",
        f"• Created: {created}",
        f"• Updated: {updated}",
        f"• Skipped: {skipped}",
        f"• Errors: {errors}",
    ]

    if duration_seconds is not None:
        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)
        message_parts.append(f"• Duration: {minutes}m {seconds}s")

    # Add error details
    if errors > 0:
        error_list = []
        for result in results:
            if result.status == 'error':
                error_list.append(f"• {result.target_org}/{result.repo_name}: {result.message}")
        if error_list:
            message_parts.append("\n*Errors:*")
            message_parts.extend(error_list[:10])  # Limit to 10 errors
            if len(error_list) > 10:
                message_parts.append(f"• ... and {len(error_list) - 10} more errors")

    # Add skipped details
    if skipped > 0:
        skipped_list = []
        for result in results:
            if result.status == 'skipped':
                skipped_list.append(f"• {result.target_org}/{result.repo_name}: {result.message}")
        if skipped_list:
            message_parts.append("\n*Skipped:*")
            message_parts.extend(skipped_list[:10])  # Limit to 10 skipped
            if len(skipped_list) > 10:
                message_parts.append(f"• ... and {len(skipped_list) - 10} more skipped")

    message = "\n".join(message_parts)

    error_list_str = "\n".join([f"{r.target_org}/{r.repo_name}: {r.message}"
                                for r in results if r.status == 'error'][:10])
    skipped_list_str = "\n".join([f"{r.target_org}/{r.repo_name}: {r.message}"
                                  for r in results if r.status == 'skipped'][:10])

    exit_code, _ = send_slack_notification(
        title,
        message,
        status=overall_status,
        template="repo_sync_summary",
        template_vars={
            "TOTAL_OPS": str(total),
            "CREATED": str(created),
            "UPDATED": str(updated),
            "SKIPPED": str(skipped),
            "ERRORS": str(errors),
            "DURATION": f"{int(duration_seconds // 60)}m {int(duration_seconds % 60)}s" if duration_seconds else "N/A",
            "ERROR_LIST": error_list_str if error_list_str else "None",
            "SKIPPED_LIST": skipped_list_str if skipped_list_str else "None"
        },
        thread_ts=thread_ts
    )

    return exit_code


def main():
    parser = argparse.ArgumentParser(
        description='Mirror repositories from source org to multiple target orgs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --config repo-sync.yaml
  %(prog)s --config repo-sync.yaml --dry-run
  %(prog)s --config repo-sync.yaml --verbose
  %(prog)s --config repo-sync.yaml --token ghp_xxxxx

Environment Variables:
  GITHUB_TOKEN    GitHub Personal Access Token (if --token not provided)
        """
    )

    parser.add_argument(
        '--config',
        default='repo-sync.yaml',
        help='Path to configuration file (default: repo-sync.yaml)'
    )

    parser.add_argument(
        '--token',
        help='GitHub Personal Access Token (or set GITHUB_TOKEN env var)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview actions without making changes'
    )

    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Get GitHub token
    token = args.token or os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Error: GitHub token required. Provide via --token or GITHUB_TOKEN env var")
        sys.exit(1)

    # Initialize syncer
    syncer = RepoSyncer(token=token, dry_run=args.dry_run, verbose=args.verbose)

    # Load configuration
    config = syncer.load_config(args.config)

    # Perform sync
    results = syncer.sync_all(config)

    # Print summary
    syncer.print_summary(results)

    # Exit with error code if any errors occurred
    errors = sum(1 for r in results if r.status == 'error')
    sys.exit(1 if errors > 0 else 0)


if __name__ == '__main__':
    main()
