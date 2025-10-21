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

# Try to import colorlog for colored output
try:
    from colorlog import ColoredFormatter
    HAS_COLORLOG = True
except ImportError:
    HAS_COLORLOG = False
    # Fallback: colorlog not available, will use standard logging


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
        """Configure logging with colors and improved formatting"""
        logger = logging.getLogger('repo-sync')
        level = logging.DEBUG if self.verbose else logging.INFO
        logger.setLevel(level)

        # Remove existing handlers to avoid duplicates
        logger.handlers.clear()

        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        # Check if output is to a terminal (TTY) for color support
        use_colors = HAS_COLORLOG and hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()

        if use_colors:
            # Use colored formatter with improved format
            formatter = ColoredFormatter(
                '[%(asctime)s] %(log_color)s%(levelname)-8s%(reset)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
                log_colors={
                    'DEBUG': 'cyan',
                    'INFO': 'green',
                    'WARNING': 'yellow',
                    'ERROR': 'red',
                    'CRITICAL': 'red,bg_white',
                },
                secondary_log_colors={},
                style='%'
            )
        else:
            # Fallback to standard formatter (for log files or when colorlog unavailable)
            formatter = logging.Formatter(
                '[%(asctime)s] %(levelname)-8s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Log colorlog availability status (only in verbose mode)
        if self.verbose:
            if use_colors:
                logger.debug("Colored logging enabled (colorlog available, TTY detected)")
            elif HAS_COLORLOG:
                logger.debug("Colored logging disabled (not a TTY, output redirected)")
            else:
                logger.debug("Colored logging unavailable (colorlog not installed)")

        return logger

    def _log_section(self, title: str, width: int = 70):
        """Log a section header with visual separator"""
        separator = "=" * width
        self.logger.info(separator)
        self.logger.info(title)
        self.logger.info(separator)

    def _format_settings_for_log(self, settings: Dict, indent: bool = False) -> str:
        """
        Format settings dictionary as JSON for log output.

        Args:
            settings: Dictionary of settings to format
            indent: If True, use pretty-printed multi-line JSON (default: False for single line)

        Returns:
            JSON-formatted string representation of settings (no truncation)
        """
        if not settings:
            return "{}"

        try:
            # Use JSON formatting for clean, readable output
            if indent:
                # Multi-line indented JSON for complex structures
                return json.dumps(settings, indent=2, sort_keys=True, default=str)
            else:
                # Single-line compact JSON
                return json.dumps(settings, sort_keys=True, default=str)
        except (TypeError, ValueError) as e:
            # Fallback if JSON serialization fails
            self.logger.debug(f"Failed to JSON serialize settings: {e}")
            return str(settings)

    def load_config(self, config_path: str) -> Config:
        """Load and validate configuration from YAML file"""
        self._log_section("Configuration Loading")
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
        """Get comprehensive repository metadata and settings"""
        try:
            repo = self.github.get_repo(f"{org}/{repo_name}")

            # Basic metadata
            metadata = {
                'description': repo.description or '',
                'homepage': repo.homepage or '',
                'topics': repo.get_topics(),
                'private': repo.private,
                'default_branch': repo.default_branch,

                # Repository features
                'has_issues': repo.has_issues,
                'has_wiki': repo.has_wiki,
                'has_projects': repo.has_projects,
                'has_discussions': repo.has_discussions,

                # Merge settings
                'allow_squash_merge': repo.allow_squash_merge,
                'allow_merge_commit': repo.allow_merge_commit,
                'allow_rebase_merge': repo.allow_rebase_merge,
                'allow_auto_merge': repo.allow_auto_merge,
                'delete_branch_on_merge': repo.delete_branch_on_merge,
                'allow_update_branch': repo.allow_update_branch if hasattr(repo, 'allow_update_branch') else None,

                # Merge commit formats
                'squash_merge_commit_title': repo.squash_merge_commit_title if hasattr(repo, 'squash_merge_commit_title') else None,
                'squash_merge_commit_message': repo.squash_merge_commit_message if hasattr(repo, 'squash_merge_commit_message') else None,
                'merge_commit_title': repo.merge_commit_title if hasattr(repo, 'merge_commit_title') else None,
                'merge_commit_message': repo.merge_commit_message if hasattr(repo, 'merge_commit_message') else None,

                # Other settings
                'allow_forking': repo.allow_forking if hasattr(repo, 'allow_forking') else None,
                'is_template': repo.is_template,
                'archived': repo.archived,
                'web_commit_signoff_required': repo.web_commit_signoff_required if hasattr(repo, 'web_commit_signoff_required') else None,
            }

            # Get GitHub Actions settings
            actions_settings = {}

            # Get Actions permissions (enabled/disabled, allowed actions)
            success, perms = self._get_repo_actions_permissions(org, repo_name)
            if success and perms:
                actions_settings['actions_permissions'] = perms

                # If allowed_actions is 'selected', get the selected actions configuration
                if perms.get('allowed_actions') == 'selected':
                    success, selected = self._get_repo_actions_selected_actions(org, repo_name)
                    if success and selected:
                        actions_settings['selected_actions'] = selected

            # Get workflow default permissions
            success, workflow_perms = self._get_repo_workflow_permissions(org, repo_name)
            if success and workflow_perms:
                actions_settings['workflow_permissions'] = workflow_perms

            # Get workflow access level (for private repos)
            success, access_level = self._get_repo_workflow_access_level(org, repo_name)
            if success and access_level:
                actions_settings['workflow_access'] = access_level

            # Add Actions settings if any were retrieved
            if actions_settings:
                metadata['actions_settings'] = actions_settings

            self.logger.debug(f"Retrieved metadata for {org}/{repo_name}")
            return metadata

        except GithubException as e:
            self.logger.error(f"Failed to get metadata for {org}/{repo_name}: {e}")
            return {}

    def _set_repo_metadata(self, org: str, repo_name: str, metadata: Dict) -> bool:
        """Set comprehensive repository metadata and settings"""
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would update metadata for {org}/{repo_name}")
            return True

        settings_synced = {'success': [], 'failed': []}

        try:
            repo = self.github.get_repo(f"{org}/{repo_name}")

            # Prepare edit parameters (only include non-None values)
            edit_params = {}

            # Basic metadata
            edit_params['description'] = metadata.get('description', '')
            edit_params['homepage'] = metadata.get('homepage', '')
            edit_params['private'] = metadata.get('private', False)

            # Repository features
            if 'has_issues' in metadata:
                edit_params['has_issues'] = metadata['has_issues']
            if 'has_wiki' in metadata:
                edit_params['has_wiki'] = metadata['has_wiki']
            if 'has_projects' in metadata:
                edit_params['has_projects'] = metadata['has_projects']
            if 'has_discussions' in metadata:
                edit_params['has_discussions'] = metadata['has_discussions']

            # Merge settings
            if 'allow_squash_merge' in metadata:
                edit_params['allow_squash_merge'] = metadata['allow_squash_merge']
            if 'allow_merge_commit' in metadata:
                edit_params['allow_merge_commit'] = metadata['allow_merge_commit']
            if 'allow_rebase_merge' in metadata:
                edit_params['allow_rebase_merge'] = metadata['allow_rebase_merge']
            if 'allow_auto_merge' in metadata:
                edit_params['allow_auto_merge'] = metadata['allow_auto_merge']
            if 'delete_branch_on_merge' in metadata:
                edit_params['delete_branch_on_merge'] = metadata['delete_branch_on_merge']
            if metadata.get('allow_update_branch') is not None:
                edit_params['allow_update_branch'] = metadata['allow_update_branch']

            # Merge commit formats
            if metadata.get('squash_merge_commit_title') is not None:
                edit_params['squash_merge_commit_title'] = metadata['squash_merge_commit_title']
            if metadata.get('squash_merge_commit_message') is not None:
                edit_params['squash_merge_commit_message'] = metadata['squash_merge_commit_message']
            if metadata.get('merge_commit_title') is not None:
                edit_params['merge_commit_title'] = metadata['merge_commit_title']
            if metadata.get('merge_commit_message') is not None:
                edit_params['merge_commit_message'] = metadata['merge_commit_message']

            # Other settings
            if metadata.get('allow_forking') is not None:
                edit_params['allow_forking'] = metadata['allow_forking']
            if 'is_template' in metadata:
                edit_params['is_template'] = metadata['is_template']
            if 'archived' in metadata:
                edit_params['archived'] = metadata['archived']
            if metadata.get('web_commit_signoff_required') is not None:
                edit_params['web_commit_signoff_required'] = metadata['web_commit_signoff_required']

            # Apply all repository settings via edit()
            try:
                repo.edit(**edit_params)
                settings_synced['success'].append('repository_settings')
                self.logger.debug(f"Updated repository settings for {org}/{repo_name}")
            except Exception as e:
                settings_synced['failed'].append(f'repository_settings: {e}')
                self.logger.warning(f"Failed to sync repository_settings for {org}/{repo_name}")
                self.logger.warning(f"  Target: {org}/{repo_name}")
                self.logger.warning(f"  Attempted: {self._format_settings_for_log(edit_params)}")
                self.logger.warning(f"  Error: {e}")

            # Update topics
            if 'topics' in metadata and metadata['topics']:
                try:
                    repo.replace_topics(metadata['topics'])
                    settings_synced['success'].append('topics')
                except Exception as e:
                    settings_synced['failed'].append(f'topics: {e}')
                    self.logger.warning(f"Failed to sync topics for {org}/{repo_name}")
                    self.logger.warning(f"  Target: {org}/{repo_name}")
                    self.logger.warning(f"  Attempted topics: {metadata['topics']}")
                    self.logger.warning(f"  Error: {e}")

            # Update default branch (if different)
            if 'default_branch' in metadata:
                current_default = repo.default_branch
                new_default = metadata['default_branch']
                if current_default != new_default:
                    # Check if the branch exists in target repo
                    try:
                        repo.get_branch(new_default)
                        repo.edit(default_branch=new_default)
                        settings_synced['success'].append('default_branch')
                        self.logger.debug(f"Updated default branch to {new_default}")
                    except GithubException as e:
                        settings_synced['failed'].append(f'default_branch: Branch {new_default} does not exist')
                        self.logger.warning(f"Failed to sync default_branch for {org}/{repo_name}")
                        self.logger.warning(f"  Target: {org}/{repo_name}")
                        self.logger.warning(f"  Attempted branch: '{new_default}'")
                        self.logger.warning(f"  Error: Branch does not exist in target repository (current: '{current_default}')")

            # Sync GitHub Actions settings
            if 'actions_settings' in metadata:
                actions_settings = metadata['actions_settings']

                # Sync Actions permissions (enabled/disabled, allowed actions)
                if 'actions_permissions' in actions_settings:
                    if self._set_repo_actions_permissions(org, repo_name, actions_settings['actions_permissions']):
                        settings_synced['success'].append('actions_permissions')
                    else:
                        settings_synced['failed'].append('actions_permissions')
                        self.logger.warning(f"Failed to sync actions_permissions for {org}/{repo_name}")
                        self.logger.warning(f"  Target: {org}/{repo_name}")
                        self.logger.warning(f"  Attempted: {self._format_settings_for_log(actions_settings['actions_permissions'])}")
                        self.logger.warning(f"  See error details in logs above")

                    # Sync selected actions (if allowed_actions is 'selected')
                    if 'selected_actions' in actions_settings:
                        if self._set_repo_actions_selected_actions(org, repo_name, actions_settings['selected_actions']):
                            settings_synced['success'].append('selected_actions')
                        else:
                            settings_synced['failed'].append('selected_actions')
                            self.logger.warning(f"Failed to sync selected_actions for {org}/{repo_name}")
                            self.logger.warning(f"  Target: {org}/{repo_name}")
                            self.logger.warning(f"  Attempted: {self._format_settings_for_log(actions_settings['selected_actions'])}")
                            self.logger.warning(f"  See error details in logs above")

                # Sync workflow permissions
                if 'workflow_permissions' in actions_settings:
                    if self._set_repo_workflow_permissions(org, repo_name, actions_settings['workflow_permissions']):
                        settings_synced['success'].append('workflow_permissions')
                    else:
                        settings_synced['failed'].append('workflow_permissions')
                        self.logger.warning(f"Failed to sync workflow_permissions for {org}/{repo_name}")
                        self.logger.warning(f"  Target: {org}/{repo_name}")
                        self.logger.warning(f"  Attempted: {self._format_settings_for_log(actions_settings['workflow_permissions'])}")
                        self.logger.warning(f"  See error details in logs above")

                # Sync workflow access level (for private repos)
                if 'workflow_access' in actions_settings:
                    if self._set_repo_workflow_access_level(org, repo_name, actions_settings['workflow_access']):
                        settings_synced['success'].append('workflow_access')
                    else:
                        settings_synced['failed'].append('workflow_access')
                        self.logger.warning(f"Failed to sync workflow_access for {org}/{repo_name}")
                        self.logger.warning(f"  Target: {org}/{repo_name}")
                        self.logger.warning(f"  Attempted: {self._format_settings_for_log(actions_settings['workflow_access'])}")
                        self.logger.warning(f"  See error details in logs above")

            # Log summary
            total_success = len(settings_synced['success'])
            total_failed = len(settings_synced['failed'])

            if total_success > 0:
                self.logger.info(f"Synced {total_success} setting group(s) for {org}/{repo_name}")
                if self.verbose:
                    self.logger.debug(f"  Synced: {', '.join(settings_synced['success'])}")

            if total_failed > 0:
                self.logger.warning(f"Failed to sync {total_failed} setting group(s) for {org}/{repo_name}")
                if self.verbose:
                    self.logger.debug(f"  Failed: {', '.join(settings_synced['failed'])}")

            # Return True if at least basic settings succeeded
            return 'repository_settings' in settings_synced['success']

        except GithubException as e:
            self.logger.error(f"Failed to set metadata for {org}/{repo_name}: {e}")
            return False

    def _create_repo(self, org: str, repo_name: str, metadata: Dict) -> bool:
        """Create a new repository in the target organization with all settings"""
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would create repository {org}/{repo_name}")
            return True

        try:
            org_obj = self.github.get_organization(org)

            # Prepare creation parameters
            create_params = {
                'name': repo_name,
                'description': metadata.get('description', ''),
                'homepage': metadata.get('homepage', ''),
                'private': metadata.get('private', False),
                'auto_init': False,  # Don't create initial commit
            }

            # Add feature flags that can be set during creation
            if 'has_issues' in metadata:
                create_params['has_issues'] = metadata['has_issues']
            if 'has_wiki' in metadata:
                create_params['has_wiki'] = metadata['has_wiki']
            if 'has_projects' in metadata:
                create_params['has_projects'] = metadata['has_projects']

            org_obj.create_repo(**create_params)
            self.logger.info(f"Created repository {org}/{repo_name}")

            # Now apply all other settings via _set_repo_metadata
            # This includes topics, merge settings, Actions settings, etc.
            self._set_repo_metadata(org, repo_name, metadata)

            return True
        except GithubException as e:
            self.logger.error(f"Failed to create repository {org}/{repo_name}: {e}")
            return False

    def _mirror_clone(self, source_org: str, repo_name: str, temp_dir: str, default_branch: str) -> bool:
        """Clone repository default branch and tags"""
        source_url = f"https://github.com/{source_org}/{repo_name}.git"
        auth_url = self._get_auth_url(source_url)

        mirror_path = os.path.join(temp_dir, f"{repo_name}.git")

        self.logger.debug(f"  → Cloning from source: {source_org}/{repo_name} (branch: {default_branch})")

        # Clone only the default branch as bare repo
        returncode, stdout, stderr = self._run_command([
            'git', 'clone', '--bare', '--single-branch', '--branch', default_branch, auth_url, mirror_path
        ])

        if returncode != 0:
            self.logger.error(f"Failed to clone {source_org}/{repo_name}")
            return False

        # Fetch all tags
        self.logger.debug(f"  → Fetching tags: {source_org}/{repo_name}")
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
        self.logger.debug(f"  → Pushing branch '{default_branch}' to {target_org}/{repo_name}")
        returncode, stdout, stderr = self._run_command([
            'git', 'push', auth_url, default_branch
        ], cwd=mirror_path)

        if returncode != 0:
            self.logger.error(f"Failed to push {default_branch} to {target_org}/{repo_name}")
            self.logger.error(f"Error: {stderr}")
            return False

        # Push tags
        self.logger.debug(f"  → Pushing tags to {target_org}/{repo_name}")
        returncode, stdout, stderr = self._run_command([
            'git', 'push', auth_url, 'refs/tags/*:refs/tags/*'
        ], cwd=mirror_path)

        if returncode != 0:
            self.logger.warning(f"Failed to push tags to {target_org}/{repo_name}: {stderr}")
            # Don't fail the whole operation if tags fail

        return True

    def _can_fast_forward(self, repo_name: str, temp_dir: str,
                         source_org: str, target_org: str, default_branch: str) -> Tuple[bool, str]:
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

        # Check if target's default branch is ancestor of source's default branch
        # In bare repos, local branches are at refs/heads/<branch> (just use <branch>)
        returncode, stdout, stderr = self._run_command([
            'git', 'merge-base', '--is-ancestor',
            f'target/{default_branch}', default_branch
        ], cwd=mirror_path)

        if returncode == 0:
            return True, "Can fast-forward"
        elif returncode == 1:
            return False, "Target has diverged from source (cannot fast-forward)"
        else:
            return False, f"Failed to check ancestry: {stderr}"

    def _check_org_actions_permissions(self, org: str) -> Tuple[bool, Optional[Dict], str]:
        """
        Check organization Actions permissions policy.

        Args:
            org: Organization name

        Returns:
            Tuple of (success, permissions_data, message)
            permissions_data contains 'enabled_repositories' and 'allowed_actions'
        """
        try:
            # Use PyGithub's requester to make API call
            headers, data = self.github._Github__requester.requestJsonAndCheck(
                "GET",
                f"/orgs/{org}/actions/permissions"
            )

            self.logger.debug(f"Organization {org} Actions permissions: {data}")

            return True, data, "Successfully retrieved Actions permissions"

        except GithubException as e:
            if e.status == 404:
                return False, None, f"Organization {org} not found or Actions not enabled"
            elif e.status == 403:
                return False, None, f"Insufficient permissions to check {org} Actions settings (requires admin:org scope)"
            else:
                return False, None, f"Failed to check Actions permissions: {e.data.get('message', str(e))}"
        except Exception as e:
            return False, None, f"Unexpected error checking Actions permissions: {str(e)}"

    def _check_org_allowed_actions(self, org: str) -> Tuple[bool, Optional[Dict], str]:
        """
        Check organization allowed actions and reusable workflows settings.
        Only applicable when allowed_actions policy is 'selected'.

        Args:
            org: Organization name

        Returns:
            Tuple of (success, allowed_actions_data, message)
            allowed_actions_data contains 'github_owned_allowed', 'verified_allowed', 'patterns_allowed'
        """
        try:
            # Use PyGithub's requester to make API call
            headers, data = self.github._Github__requester.requestJsonAndCheck(
                "GET",
                f"/orgs/{org}/actions/permissions/selected-actions"
            )

            self.logger.debug(f"Organization {org} allowed actions: {data}")

            return True, data, "Successfully retrieved allowed actions settings"

        except GithubException as e:
            if e.status == 404:
                return False, None, f"Selected actions not configured for {org}"
            elif e.status == 403:
                return False, None, f"Insufficient permissions to check {org} allowed actions"
            else:
                return False, None, f"Failed to check allowed actions: {e.data.get('message', str(e))}"
        except Exception as e:
            return False, None, f"Unexpected error checking allowed actions: {str(e)}"

    def _check_repo_workflow_access(self, org: str, repo_name: str) -> Tuple[bool, Optional[str], str]:
        """
        Check repository workflow access settings for private repositories.
        This determines if other repos in the org can use workflows from this repo.

        Args:
            org: Organization name
            repo_name: Repository name

        Returns:
            Tuple of (success, access_level, message)
            access_level can be 'none', 'organization', 'enterprise', 'user'
        """
        try:
            repo = self.github.get_repo(f"{org}/{repo_name}")

            # Only check access settings for private repositories
            if not repo.private:
                return True, "public", "Repository is public - workflows are accessible to all"

            # Use PyGithub's requester to make API call
            headers, data = self.github._Github__requester.requestJsonAndCheck(
                "GET",
                f"/repos/{org}/{repo_name}/actions/permissions/access"
            )

            access_level = data.get('access_level', 'unknown')
            self.logger.debug(f"Repository {org}/{repo_name} workflow access: {access_level}")

            return True, access_level, f"Workflow access level: {access_level}"

        except GithubException as e:
            if e.status == 404:
                # Repo doesn't exist or endpoint not available
                return False, None, f"Repository {org}/{repo_name} not found or access settings not configured"
            elif e.status == 403:
                return False, None, f"Insufficient permissions to check workflow access for {org}/{repo_name}"
            else:
                return False, None, f"Failed to check workflow access: {e.data.get('message', str(e))}"
        except Exception as e:
            return False, None, f"Unexpected error checking workflow access: {str(e)}"

    def verify_workflow_permissions(self, source_org: str, target_orgs: List[str],
                                   source_workflow_repo: Optional[str] = None) -> Dict[str, List[str]]:
        """
        Verify that organizations have compatible settings for reusable workflows.

        Args:
            source_org: Source organization containing the workflow repository
            target_orgs: List of target organizations
            source_workflow_repo: Name of the repository containing reusable workflows (optional)

        Returns:
            Dictionary with 'warnings' key containing list of warning messages
        """
        warnings = []

        self._log_section("Workflow Permissions Verification")

        # Check source organization
        self.logger.info("")
        self.logger.info(f"Checking source organization: {source_org}")
        success, perms, msg = self._check_org_actions_permissions(source_org)

        if not success:
            warning = f"⚠️  Source org {source_org}: {msg}"
            self.logger.warning(warning)
            warnings.append(warning)
        else:
            enabled_repos = perms.get('enabled_repositories', 'unknown')
            allowed_actions = perms.get('allowed_actions', 'unknown')

            self.logger.info(f"  Enabled repositories: {enabled_repos}")
            self.logger.info(f"  Allowed actions: {allowed_actions}")

            # Check if actions are properly enabled
            if enabled_repos not in ['all', 'selected']:
                warning = (f"⚠️  Source org {source_org}: Enabled repositories is '{enabled_repos}'. "
                          f"Should be 'all' or 'selected' to use Actions.")
                self.logger.warning(warning)
                warnings.append(warning)

            # Check allowed actions policy
            if allowed_actions == 'selected':
                self.logger.info(f"  Checking selected actions configuration...")
                success, allowed, msg = self._check_org_allowed_actions(source_org)

                if success:
                    github_owned = allowed.get('github_owned_allowed', False)
                    verified = allowed.get('verified_allowed', False)
                    patterns = allowed.get('patterns_allowed', [])

                    self.logger.info(f"    GitHub-owned actions allowed: {github_owned}")
                    self.logger.info(f"    Verified actions allowed: {verified}")
                    self.logger.info(f"    Custom patterns: {patterns if patterns else 'None'}")

                    # Check if organization workflows are likely allowed
                    org_pattern_found = any(source_org in pattern for pattern in patterns) if patterns else False
                    if not org_pattern_found and not github_owned:
                        warning = (f"⚠️  Source org {source_org}: Selected actions policy may not include "
                                  f"organization workflows. Consider adding pattern '{source_org}/*' "
                                  f"or setting allowed_actions to 'all'.")
                        self.logger.warning(warning)
                        warnings.append(warning)

        # Check source workflow repository access if specified
        if source_workflow_repo:
            self.logger.info("")
            self.logger.info(f"Checking source workflow repository: {source_org}/{source_workflow_repo}")
            success, access_level, msg = self._check_repo_workflow_access(source_org, source_workflow_repo)

            if not success:
                warning = f"⚠️  Workflow repo {source_org}/{source_workflow_repo}: {msg}"
                self.logger.warning(warning)
                warnings.append(warning)
            else:
                self.logger.info(f"  {msg}")

                # Check if access is properly configured for organization use
                if access_level == 'none':
                    warning = (f"⚠️  Workflow repo {source_org}/{source_workflow_repo}: "
                              f"Access level is 'none'. Other repositories cannot use workflows from this repo. "
                              f"Set access to 'organization' in repository Settings > Actions > General > Access.")
                    self.logger.warning(warning)
                    warnings.append(warning)
                elif access_level in ['organization', 'enterprise']:
                    self.logger.info(f"  ✓ Workflow repository is accessible within the organization")
                elif access_level == 'public':
                    self.logger.info(f"  ✓ Repository is public - workflows are accessible to all")

        # Check target organizations
        for target_org in target_orgs:
            self.logger.info("")
            self.logger.info(f"Checking target organization: {target_org}")
            success, perms, msg = self._check_org_actions_permissions(target_org)

            if not success:
                warning = f"⚠️  Target org {target_org}: {msg}"
                self.logger.warning(warning)
                warnings.append(warning)
            else:
                enabled_repos = perms.get('enabled_repositories', 'unknown')
                allowed_actions = perms.get('allowed_actions', 'unknown')

                self.logger.info(f"  Enabled repositories: {enabled_repos}")
                self.logger.info(f"  Allowed actions: {allowed_actions}")

                # Check if actions are properly enabled
                if enabled_repos not in ['all', 'selected']:
                    warning = (f"⚠️  Target org {target_org}: Enabled repositories is '{enabled_repos}'. "
                              f"Should be 'all' or 'selected' to use Actions.")
                    self.logger.warning(warning)
                    warnings.append(warning)

                # Check if reusable workflows can be used
                if allowed_actions not in ['all', 'selected']:
                    warning = (f"⚠️  Target org {target_org}: Allowed actions is '{allowed_actions}'. "
                              f"Should be 'all' or 'selected' to use reusable workflows. "
                              f"Update in organization Settings > Actions > General > Policies.")
                    self.logger.warning(warning)
                    warnings.append(warning)
                elif allowed_actions == 'selected':
                    # Check selected actions configuration
                    success, allowed, msg = self._check_org_allowed_actions(target_org)

                    if success:
                        patterns = allowed.get('patterns_allowed', [])

                        # For same-org workflows, check if org pattern exists
                        if target_org == source_org:
                            org_pattern_found = any(target_org in pattern for pattern in patterns) if patterns else False
                            if not org_pattern_found:
                                warning = (f"⚠️  Target org {target_org}: Selected actions policy should include "
                                          f"pattern '{target_org}/*' to use organization workflows.")
                                self.logger.warning(warning)
                                warnings.append(warning)
                            else:
                                self.logger.info(f"  ✓ Organization pattern found in allowed actions")

        # Summary
        separator = "=" * 70
        self.logger.info("")
        self.logger.info(separator)
        if warnings:
            self.logger.warning(f"Verification Complete: {len(warnings)} warning(s) found")
            self.logger.info("")
            self.logger.warning("  Recommendations:")
            self.logger.warning("  → Ensure organizations have Actions enabled (Settings > Actions > General)")
            self.logger.warning("  → Set 'Allowed actions' to 'all' or configure 'selected' with appropriate patterns")
            self.logger.warning("  → For workflow source repos, set Access to 'organization' (Repo Settings > Actions > General)")
            self.logger.warning("  → See: https://docs.github.com/en/organizations/managing-organization-settings/disabling-or-limiting-github-actions-for-your-organization")
        else:
            self.logger.info("✓ Verification Complete: No issues found")
        self.logger.info(separator)
        self.logger.info("")

        return {'warnings': warnings}

    def _get_repo_actions_permissions(self, org: str, repo_name: str) -> Tuple[bool, Optional[Dict]]:
        """
        Get repository Actions permissions (enabled status and allowed actions policy).

        Args:
            org: Organization name
            repo_name: Repository name

        Returns:
            Tuple of (success, settings_dict)
            settings_dict contains: enabled, allowed_actions, sha_pinning_required
        """
        try:
            headers, data = self.github._Github__requester.requestJsonAndCheck(
                "GET",
                f"/repos/{org}/{repo_name}/actions/permissions"
            )
            self.logger.debug(f"Repository {org}/{repo_name} Actions permissions: {data}")
            return True, data
        except GithubException as e:
            if e.status == 404:
                self.logger.debug(f"Actions not configured for {org}/{repo_name}")
                return False, None
            elif e.status == 403:
                self.logger.debug(f"Insufficient permissions to read Actions settings for {org}/{repo_name}")
                return False, None
            else:
                self.logger.warning(f"Failed to get Actions permissions for {org}/{repo_name}: {e}")
                return False, None
        except Exception as e:
            self.logger.warning(f"Unexpected error getting Actions permissions: {e}")
            return False, None

    def _set_repo_actions_permissions(self, org: str, repo_name: str, settings: Dict) -> bool:
        """
        Set repository Actions permissions.

        Args:
            org: Organization name
            repo_name: Repository name
            settings: Dict with enabled, allowed_actions, sha_pinning_required

        Returns:
            True if successful, False otherwise
        """
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would set Actions permissions for {org}/{repo_name}: {settings}")
            return True

        try:
            self.github._Github__requester.requestJsonAndCheck(
                "PUT",
                f"/repos/{org}/{repo_name}/actions/permissions",
                input=settings
            )
            self.logger.debug(f"Set Actions permissions for {org}/{repo_name}")
            return True
        except GithubException as e:
            self.logger.warning(f"Failed to set Actions permissions for {org}/{repo_name}")
            self.logger.warning(f"  Target: {org}/{repo_name}")
            self.logger.warning(f"  Attempted: {self._format_settings_for_log(settings)}")
            if e.status == 403:
                self.logger.warning(f"  Error: 403 Forbidden - Insufficient permissions or Actions disabled at organization level")
            else:
                self.logger.warning(f"  Error: {e}")
            return False
        except Exception as e:
            self.logger.warning(f"Failed to set Actions permissions for {org}/{repo_name}")
            self.logger.warning(f"  Target: {org}/{repo_name}")
            self.logger.warning(f"  Error: Unexpected error - {e}")
            return False

    def _get_repo_actions_selected_actions(self, org: str, repo_name: str) -> Tuple[bool, Optional[Dict]]:
        """
        Get repository selected actions configuration (only if allowed_actions='selected').

        Args:
            org: Organization name
            repo_name: Repository name

        Returns:
            Tuple of (success, settings_dict)
            settings_dict contains: github_owned_allowed, verified_allowed, patterns_allowed
        """
        try:
            headers, data = self.github._Github__requester.requestJsonAndCheck(
                "GET",
                f"/repos/{org}/{repo_name}/actions/permissions/selected-actions"
            )
            self.logger.debug(f"Repository {org}/{repo_name} selected actions: {data}")
            return True, data
        except GithubException as e:
            if e.status == 404:
                self.logger.debug(f"Selected actions not configured for {org}/{repo_name}")
                return False, None
            else:
                self.logger.debug(f"Failed to get selected actions for {org}/{repo_name}: {e}")
                return False, None
        except Exception as e:
            self.logger.warning(f"Unexpected error getting selected actions: {e}")
            return False, None

    def _set_repo_actions_selected_actions(self, org: str, repo_name: str, settings: Dict) -> bool:
        """
        Set repository selected actions configuration.

        Args:
            org: Organization name
            repo_name: Repository name
            settings: Dict with github_owned_allowed, verified_allowed, patterns_allowed

        Returns:
            True if successful, False otherwise
        """
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would set selected actions for {org}/{repo_name}: {settings}")
            return True

        try:
            self.github._Github__requester.requestJsonAndCheck(
                "PUT",
                f"/repos/{org}/{repo_name}/actions/permissions/selected-actions",
                input=settings
            )
            self.logger.debug(f"Set selected actions for {org}/{repo_name}")
            return True
        except GithubException as e:
            self.logger.warning(f"Failed to set selected actions for {org}/{repo_name}")
            self.logger.warning(f"  Target: {org}/{repo_name}")
            self.logger.warning(f"  Attempted: {self._format_settings_for_log(settings)}")
            self.logger.warning(f"  Error: {e}")
            return False
        except Exception as e:
            self.logger.warning(f"Failed to set selected actions for {org}/{repo_name}")
            self.logger.warning(f"  Target: {org}/{repo_name}")
            self.logger.warning(f"  Error: Unexpected error - {e}")
            return False

    def _get_repo_workflow_permissions(self, org: str, repo_name: str) -> Tuple[bool, Optional[Dict]]:
        """
        Get repository default workflow permissions for GITHUB_TOKEN.

        Args:
            org: Organization name
            repo_name: Repository name

        Returns:
            Tuple of (success, settings_dict)
            settings_dict contains: default_workflow_permissions, can_approve_pull_request_reviews
        """
        try:
            headers, data = self.github._Github__requester.requestJsonAndCheck(
                "GET",
                f"/repos/{org}/{repo_name}/actions/permissions/workflow"
            )
            self.logger.debug(f"Repository {org}/{repo_name} workflow permissions: {data}")
            return True, data
        except GithubException as e:
            if e.status == 404:
                self.logger.debug(f"Workflow permissions not configured for {org}/{repo_name}")
                return False, None
            else:
                self.logger.debug(f"Failed to get workflow permissions for {org}/{repo_name}: {e}")
                return False, None
        except Exception as e:
            self.logger.warning(f"Unexpected error getting workflow permissions: {e}")
            return False, None

    def _set_repo_workflow_permissions(self, org: str, repo_name: str, settings: Dict) -> bool:
        """
        Set repository default workflow permissions for GITHUB_TOKEN.

        Args:
            org: Organization name
            repo_name: Repository name
            settings: Dict with default_workflow_permissions, can_approve_pull_request_reviews

        Returns:
            True if successful, False otherwise
        """
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would set workflow permissions for {org}/{repo_name}: {settings}")
            return True

        try:
            self.github._Github__requester.requestJsonAndCheck(
                "PUT",
                f"/repos/{org}/{repo_name}/actions/permissions/workflow",
                input=settings
            )
            self.logger.debug(f"Set workflow permissions for {org}/{repo_name}")
            return True
        except GithubException as e:
            self.logger.warning(f"Failed to set workflow permissions for {org}/{repo_name}")
            self.logger.warning(f"  Target: {org}/{repo_name}")
            self.logger.warning(f"  Attempted: {self._format_settings_for_log(settings)}")
            self.logger.warning(f"  Error: {e}")
            return False
        except Exception as e:
            self.logger.warning(f"Failed to set workflow permissions for {org}/{repo_name}")
            self.logger.warning(f"  Target: {org}/{repo_name}")
            self.logger.warning(f"  Error: Unexpected error - {e}")
            return False

    def _get_repo_workflow_access_level(self, org: str, repo_name: str) -> Tuple[bool, Optional[Dict]]:
        """
        Get repository workflow access level (for private repos).

        Args:
            org: Organization name
            repo_name: Repository name

        Returns:
            Tuple of (success, settings_dict)
            settings_dict contains: access_level
        """
        try:
            # First check if repo is private
            repo = self.github.get_repo(f"{org}/{repo_name}")
            if not repo.private:
                self.logger.debug(f"Repository {org}/{repo_name} is public, skipping access level")
                return True, {'access_level': 'public'}

            headers, data = self.github._Github__requester.requestJsonAndCheck(
                "GET",
                f"/repos/{org}/{repo_name}/actions/permissions/access"
            )
            self.logger.debug(f"Repository {org}/{repo_name} workflow access level: {data}")
            return True, data
        except GithubException as e:
            if e.status == 404:
                self.logger.debug(f"Workflow access level not configured for {org}/{repo_name}")
                return False, None
            else:
                self.logger.debug(f"Failed to get workflow access level for {org}/{repo_name}: {e}")
                return False, None
        except Exception as e:
            self.logger.warning(f"Unexpected error getting workflow access level: {e}")
            return False, None

    def _set_repo_workflow_access_level(self, org: str, repo_name: str, settings: Dict) -> bool:
        """
        Set repository workflow access level (for private repos).

        Args:
            org: Organization name
            repo_name: Repository name
            settings: Dict with access_level

        Returns:
            True if successful, False otherwise
        """
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would set workflow access level for {org}/{repo_name}: {settings}")
            return True

        try:
            # Skip if public repo
            if settings.get('access_level') == 'public':
                self.logger.debug(f"Repository {org}/{repo_name} is public, skipping access level")
                return True

            self.github._Github__requester.requestJsonAndCheck(
                "PUT",
                f"/repos/{org}/{repo_name}/actions/permissions/access",
                input=settings
            )
            self.logger.debug(f"Set workflow access level for {org}/{repo_name}")
            return True
        except GithubException as e:
            self.logger.warning(f"Failed to set workflow access level for {org}/{repo_name}")
            self.logger.warning(f"  Target: {org}/{repo_name}")
            self.logger.warning(f"  Attempted: {self._format_settings_for_log(settings)}")
            self.logger.warning(f"  Error: {e}")
            return False
        except Exception as e:
            self.logger.warning(f"Failed to set workflow access level for {org}/{repo_name}")
            self.logger.warning(f"  Target: {org}/{repo_name}")
            self.logger.warning(f"  Error: Unexpected error - {e}")
            return False

    def sync_repository(self, source_org: str, repo_name: str,
                       target_org: str) -> SyncResult:
        """
        Sync a single repository from source to target organization.
        Returns SyncResult with status and message.
        """
        self.logger.debug(f"Starting sync: {repo_name} ({source_org} → {target_org})")

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
                    repo_name, temp_dir, source_org, target_org, default_branch
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

        self._log_section("Repository Sync")
        self.logger.info(f"Starting sync: {len(config.repositories)} repositories → "
                        f"{len(config.target_orgs)} target organizations ({total_syncs} operations)")

        # Verify workflow permissions before syncing
        # Try to detect if any repository contains workflows (look for common workflow repo names)
        workflow_repo_candidates = [
            'github-workflows', '.github', 'workflows', 'ci-workflows',
            'shared-workflows', 'reusable-workflows'
        ]
        detected_workflow_repo = None
        for repo in config.repositories:
            repo_lower = repo.lower()
            if any(candidate in repo_lower for candidate in workflow_repo_candidates):
                detected_workflow_repo = repo
                break

        try:
            verification_result = self.verify_workflow_permissions(
                config.source_org,
                config.target_orgs,
                source_workflow_repo=detected_workflow_repo
            )

            # Log warnings but continue with sync
            if verification_result.get('warnings'):
                self.logger.info("")
                self.logger.warning(f"⚠️  {len(verification_result['warnings'])} permission warning(s) detected.")
                self.logger.warning("Continuing with sync, but workflows may not function correctly.")
                self.logger.warning("Review the warnings above and update organization/repository settings as needed.")
                self.logger.info("")
        except Exception as e:
            self.logger.warning(f"⚠️  Failed to verify workflow permissions: {e}")
            self.logger.warning("Continuing with sync anyway...")
            self.logger.info("")

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
                self.logger.info(f"[{current}/{total_syncs}] Syncing: {repo_name} ({config.source_org} → {target_org})")

                result = self.sync_repository(config.source_org, repo_name, target_org)
                results.append(result)

                # Log result with clear visual indicators
                if result.status == 'created':
                    self.logger.info(f"  ✓ Created: {target_org}/{repo_name}")
                elif result.status == 'updated':
                    self.logger.info(f"  ✓ Updated: {target_org}/{repo_name}")
                elif result.status == 'skipped':
                    self.logger.warning(f"  ⊘ Skipped: {target_org}/{repo_name} → {result.message}")
                elif result.status == 'error':
                    self.logger.error(f"  ✗ Error: {target_org}/{repo_name} → {result.message}")

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
        """Print summary of sync results with improved formatting"""
        total = len(results)
        created = sum(1 for r in results if r.status == 'created')
        updated = sum(1 for r in results if r.status == 'updated')
        skipped = sum(1 for r in results if r.status == 'skipped')
        errors = sum(1 for r in results if r.status == 'error')

        # Add spacing before summary
        print()
        self._log_section("Sync Summary")

        # Use aligned formatting for better readability
        self.logger.info(f"Total operations:  {total:>3}")
        self.logger.info(f"Created:           {created:>3}")
        self.logger.info(f"Updated:           {updated:>3}")
        self.logger.info(f"Skipped:           {skipped:>3}")
        self.logger.info(f"Errors:            {errors:>3}")

        # Add separator
        self.logger.info("=" * 70)

        # Show details for errors
        if errors > 0:
            self.logger.info("")
            self.logger.error("Errors encountered:")
            for result in results:
                if result.status == 'error':
                    self.logger.error(f"  → {result.target_org}/{result.repo_name}: {result.message}")

        # Show details for skipped repos
        if skipped > 0:
            self.logger.info("")
            self.logger.warning("Skipped repositories:")
            for result in results:
                if result.status == 'skipped':
                    self.logger.warning(f"  → {result.target_org}/{result.repo_name}: {result.message}")


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
