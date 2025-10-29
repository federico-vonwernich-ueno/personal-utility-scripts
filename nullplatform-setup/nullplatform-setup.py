#!/usr/bin/env python3
"""
Nullplatform Setup Script

Automates the creation of applications, parameters, and scopes in nullplatform
using the np CLI based on a YAML configuration file.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install it with: pip install PyYAML")
    sys.exit(1)


@dataclass
class SetupResult:
    """Result of setting up a single resource"""
    resource_type: str  # 'application', 'parameter', 'scope', 'namespace'
    resource_name: str
    status: str  # 'created', 'exists', 'error'
    message: str
    resource_id: Optional[str] = None


@dataclass
class Config:
    """Configuration for nullplatform setup"""
    applications: List[Dict]  # Each application contains nested scopes and parameters


class NullplatformSetup:
    """Handles nullplatform resource creation via np CLI"""

    def __init__(self, api_key: Optional[str] = None, dry_run: bool = False,
                 verbose: bool = False, np_path: str = "np"):
        self.api_key = api_key or os.environ.get('NULLPLATFORM_API_KEY')
        self.dry_run = dry_run
        self.verbose = verbose
        self.np_path = np_path
        self.logger = self._setup_logger()

        # Track created resource IDs for dependencies
        self.resource_ids = {
            'applications': {},  # name -> id
            'parameters': {},    # name -> id
            'scopes': {}         # name -> id
        }

    def _setup_logger(self) -> logging.Logger:
        """Configure logging"""
        logger = logging.getLogger('nullplatform-setup')
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

    def _run_np_command(self, command: List[str], json_body: Optional[Dict] = None) -> Tuple[int, str, str]:
        """
        Run an np CLI command and return (returncode, stdout, stderr)

        Args:
            command: Command parts (e.g., ['application', 'create'])
            json_body: Optional JSON body to pass via --body
        """
        cmd = [self.np_path] + command

        # Add API key if provided
        if self.api_key:
            cmd.extend(['--api-key', self.api_key])

        # Add format json for easier parsing
        cmd.extend(['--format', 'json'])

        # Add JSON body if provided
        if json_body:
            # Write JSON to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(json_body, f)
                temp_file = f.name

            cmd.extend(['--body', temp_file])

        self.logger.debug(f"Running: {' '.join(cmd)}")
        if json_body:
            self.logger.debug(f"Body: {json.dumps(json_body, indent=2)}")

        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would execute: {' '.join(cmd)}")
            if json_body:
                self.logger.info(f"[DRY RUN] With body: {json.dumps(json_body, indent=2)}")
            return 0, '{"id": "dry-run-id"}', ''

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )

        # Clean up temp file
        if json_body:
            try:
                os.unlink(temp_file)
            except Exception:
                pass

        if result.returncode != 0:
            self.logger.debug(f"Command failed with code {result.returncode}")
            self.logger.debug(f"stderr: {result.stderr}")

        return result.returncode, result.stdout, result.stderr

    def _resolve_namespace_id(self, namespace_name: str) -> str:
        """
        Look up namespace ID by name from existing namespaces.

        Args:
            namespace_name: Name of the namespace

        Returns:
            Namespace ID

        Raises:
            ValueError if namespace not found
        """
        self.logger.debug(f"Resolving namespace '{namespace_name}' to ID")

        returncode, stdout, stderr = self._run_np_command(['namespace', 'list'])

        if returncode != 0:
            raise ValueError(
                f"Failed to list namespaces: {stderr}"
            )

        try:
            namespaces = json.loads(stdout)
        except json.JSONDecodeError:
            raise ValueError(
                f"Failed to parse namespace list response"
            )

        # Find namespace by name
        for ns in namespaces:
            if ns.get('name') == namespace_name:
                namespace_id = ns.get('id')
                self.logger.debug(f"Resolved namespace '{namespace_name}' to ID: {namespace_id}")
                return namespace_id

        # Namespace not found - provide helpful error
        available_names = [ns.get('name') for ns in namespaces if ns.get('name')]
        if available_names:
            raise ValueError(
                f"Namespace '{namespace_name}' not found. "
                f"Available namespaces: {', '.join(available_names)}"
            )
        else:
            raise ValueError(
                f"Namespace '{namespace_name}' not found. No namespaces available. "
                f"Create one first: np namespace create --body '{{\"name\":\"{namespace_name}\"}}'"
            )

    def load_config(self, config_path: str) -> Config:
        """Load and validate configuration from YAML file"""
        self.logger.info(f"Loading configuration from {config_path}")

        try:
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f)
        except Exception as e:
            self.logger.error(f"Failed to load config file: {e}")
            sys.exit(1)

        config = Config(
            applications=data.get('applications', [])
        )

        self.logger.info(f"Config loaded: {len(config.applications)} applications")

        return config

    def create_application(self, app_config: Dict) -> SetupResult:
        """Create an application"""
        name = app_config.get('name')

        self.logger.info(f"Creating application: {name}")

        returncode, stdout, stderr = self._run_np_command(
            ['application', 'create'],
            json_body=app_config
        )

        if returncode == 0:
            try:
                response = json.loads(stdout)
                app_id = response.get('id')
                self.resource_ids['applications'][name] = app_id

                self.logger.info(f"✓ Created application: {name} (ID: {app_id})")
                return SetupResult(
                    resource_type='application',
                    resource_name=name,
                    status='created',
                    message='Application created successfully',
                    resource_id=app_id
                )
            except json.JSONDecodeError:
                self.logger.error(f"Failed to parse response: {stdout}")
                return SetupResult(
                    resource_type='application',
                    resource_name=name,
                    status='error',
                    message=f'Failed to parse response: {stdout}'
                )
        else:
            # Check if application already exists
            if 'already exists' in stderr.lower():
                self.logger.warning(f"Application {name} already exists")
                # Try to get existing application ID
                returncode, stdout, stderr = self._run_np_command(['application', 'list'])
                if returncode == 0:
                    try:
                        apps = json.loads(stdout)
                        for app in apps:
                            if app.get('name') == name:
                                self.resource_ids['applications'][name] = app.get('id')
                                break
                    except Exception:
                        pass

                return SetupResult(
                    resource_type='application',
                    resource_name=name,
                    status='exists',
                    message='Application already exists'
                )

            self.logger.error(f"Failed to create application: {stderr}")
            return SetupResult(
                resource_type='application',
                resource_name=name,
                status='error',
                message=f'Error: {stderr}'
            )

    def create_parameter(self, param_config: Dict) -> SetupResult:
        """Create a parameter and optionally set its value"""
        name = param_config.get('name')

        self.logger.info(f"Creating parameter: {name}")

        # First create the parameter definition
        param_def = {k: v for k, v in param_config.items() if k != 'value'}

        returncode, stdout, stderr = self._run_np_command(
            ['parameter', 'create'],
            json_body=param_def
        )

        if returncode == 0:
            try:
                response = json.loads(stdout)
                param_id = response.get('id')
                self.resource_ids['parameters'][name] = param_id

                self.logger.info(f"✓ Created parameter: {name} (ID: {param_id})")

                # If value is provided, create parameter value
                if 'value' in param_config:
                    value_config = {
                        'value': param_config['value']
                    }

                    # Add scope_id if provided
                    if 'scope_id' in param_config:
                        value_config['scope_id'] = param_config['scope_id']

                    value_returncode, value_stdout, value_stderr = self._run_np_command(
                        ['parameter', 'value', 'create', '--id', param_id],
                        json_body=value_config
                    )

                    if value_returncode == 0:
                        self.logger.info(f"✓ Set value for parameter: {name}")
                        return SetupResult(
                            resource_type='parameter',
                            resource_name=name,
                            status='created',
                            message='Parameter created and value set successfully',
                            resource_id=param_id
                        )
                    else:
                        self.logger.warning(f"Created parameter but failed to set value: {value_stderr}")
                        return SetupResult(
                            resource_type='parameter',
                            resource_name=name,
                            status='created',
                            message=f'Parameter created but value not set: {value_stderr}',
                            resource_id=param_id
                        )

                return SetupResult(
                    resource_type='parameter',
                    resource_name=name,
                    status='created',
                    message='Parameter created successfully',
                    resource_id=param_id
                )
            except json.JSONDecodeError:
                self.logger.error(f"Failed to parse response: {stdout}")
                return SetupResult(
                    resource_type='parameter',
                    resource_name=name,
                    status='error',
                    message=f'Failed to parse response: {stdout}'
                )
        else:
            # Check if parameter already exists
            if 'already exists' in stderr.lower():
                self.logger.warning(f"Parameter {name} already exists")
                return SetupResult(
                    resource_type='parameter',
                    resource_name=name,
                    status='exists',
                    message='Parameter already exists'
                )

            self.logger.error(f"Failed to create parameter: {stderr}")
            return SetupResult(
                resource_type='parameter',
                resource_name=name,
                status='error',
                message=f'Error: {stderr}'
            )

    def create_scope(self, scope_config: Dict) -> SetupResult:
        """Create a scope"""
        name = scope_config.get('name')

        self.logger.info(f"Creating scope: {name}")

        returncode, stdout, stderr = self._run_np_command(
            ['scope', 'create'],
            json_body=scope_config
        )

        if returncode == 0:
            try:
                response = json.loads(stdout)
                scope_id = response.get('id')
                self.resource_ids['scopes'][name] = scope_id

                self.logger.info(f"✓ Created scope: {name} (ID: {scope_id})")
                return SetupResult(
                    resource_type='scope',
                    resource_name=name,
                    status='created',
                    message='Scope created successfully',
                    resource_id=scope_id
                )
            except json.JSONDecodeError:
                self.logger.error(f"Failed to parse response: {stdout}")
                return SetupResult(
                    resource_type='scope',
                    resource_name=name,
                    status='error',
                    message=f'Failed to parse response: {stdout}'
                )
        else:
            # Check if scope already exists
            if 'already exists' in stderr.lower():
                self.logger.warning(f"Scope {name} already exists")
                # Try to get existing scope ID
                returncode, stdout, stderr = self._run_np_command(['scope', 'list'])
                if returncode == 0:
                    try:
                        scopes = json.loads(stdout)
                        for scope in scopes:
                            if scope.get('name') == name:
                                self.resource_ids['scopes'][name] = scope.get('id')
                                break
                    except Exception:
                        pass

                return SetupResult(
                    resource_type='scope',
                    resource_name=name,
                    status='exists',
                    message='Scope already exists'
                )

            self.logger.error(f"Failed to create scope: {stderr}")
            return SetupResult(
                resource_type='scope',
                resource_name=name,
                status='error',
                message=f'Error: {stderr}'
            )

    def setup_all(self, config: Config) -> List[SetupResult]:
        """
        Setup all resources from config with nested structure.
        Each application contains its own scopes and parameters.
        Returns list of SetupResult objects.
        """
        import time

        results = []
        start_time = time.time()

        # Send Slack start notification
        thread_ts = None
        try:
            slack_rc, thread_ts = send_setup_start_notification(config)
            if slack_rc == 0:
                self.logger.debug("[SLACK] Setup start notification sent successfully")
            elif slack_rc not in (2, 3, 4):  # Ignore missing deps/config errors
                self.logger.debug(f"[SLACK] Start notification failed with code {slack_rc}")
        except Exception as e:
            self.logger.debug(f"[SLACK] Failed to send start notification: {e}")

        # Process each application with its nested resources
        for app_config in config.applications:
            app_name = app_config.get('name')
            self.logger.info(f"Processing application: {app_name}")

            # 1. Resolve namespace reference to ID
            if 'namespace' in app_config:
                namespace_name = app_config['namespace']
                try:
                    namespace_id = self._resolve_namespace_id(namespace_name)
                    app_config['namespace_id'] = namespace_id
                    self.logger.debug(f"Resolved namespace '{namespace_name}' to {namespace_id}")
                except ValueError as e:
                    self.logger.error(str(e))
                    result = SetupResult(
                        resource_type='application',
                        resource_name=app_name,
                        status='error',
                        message=str(e)
                    )
                    results.append(result)

                    # Send Slack notification for error
                    try:
                        send_resource_notification(result, thread_ts=thread_ts)
                    except Exception:
                        pass

                    continue  # Skip this application and its resources

            # 2. Create application
            app_result = self.create_application(app_config)
            results.append(app_result)

            # Send Slack notification for application
            try:
                slack_rc = send_resource_notification(app_result, thread_ts=thread_ts)
                if slack_rc == 0:
                    self.logger.debug(f"[SLACK] Resource notification sent for {app_result.resource_name}")
                elif slack_rc not in (2, 3, 4):
                    self.logger.debug(f"[SLACK] Resource notification failed with code {slack_rc}")
            except Exception as e:
                self.logger.debug(f"[SLACK] Failed to send resource notification: {e}")

            if app_result.status == 'error':
                self.logger.error(f"Failed to create application {app_name}, skipping its scopes and parameters")
                continue

            app_id = app_result.resource_id

            # 3. Create scopes for this application
            scopes = app_config.get('scopes', [])
            for scope_config in scopes:
                scope_config['application_id'] = app_id
                scope_result = self.create_scope(scope_config)
                results.append(scope_result)

                # Send Slack notification for scope
                try:
                    slack_rc = send_resource_notification(scope_result, thread_ts=thread_ts)
                    if slack_rc == 0:
                        self.logger.debug(f"[SLACK] Resource notification sent for {scope_result.resource_name}")
                    elif slack_rc not in (2, 3, 4):
                        self.logger.debug(f"[SLACK] Resource notification failed with code {slack_rc}")
                except Exception as e:
                    self.logger.debug(f"[SLACK] Failed to send resource notification: {e}")

            # 4. Create parameters for this application
            parameters = app_config.get('parameters', [])
            for param_config in parameters:
                param_config['application_id'] = app_id

                # Resolve scope reference if present
                if 'scope' in param_config:
                    scope_name = param_config['scope']
                    scope_id = self.resource_ids['scopes'].get(scope_name)

                    if scope_id:
                        param_config['scope_id'] = scope_id
                        self.logger.debug(f"Resolved scope '{scope_name}' to {scope_id}")
                    else:
                        self.logger.warning(
                            f"Scope '{scope_name}' not found for parameter '{param_config.get('name')}' "
                            f"in application '{app_name}'"
                        )

                param_result = self.create_parameter(param_config)
                results.append(param_result)

                # Send Slack notification for parameter
                try:
                    slack_rc = send_resource_notification(param_result, thread_ts=thread_ts)
                    if slack_rc == 0:
                        self.logger.debug(f"[SLACK] Resource notification sent for {param_result.resource_name}")
                    elif slack_rc not in (2, 3, 4):
                        self.logger.debug(f"[SLACK] Resource notification failed with code {slack_rc}")
                except Exception as e:
                    self.logger.debug(f"[SLACK] Failed to send resource notification: {e}")

        # Calculate duration
        duration = time.time() - start_time

        # Send Slack summary notification
        try:
            slack_rc = send_setup_summary_notification(config, results, duration, thread_ts)
            if slack_rc == 0:
                self.logger.debug("[SLACK] Summary notification sent successfully")
            elif slack_rc not in (2, 3, 4):
                self.logger.debug(f"[SLACK] Summary notification failed with code {slack_rc}")
        except Exception as e:
            self.logger.debug(f"[SLACK] Failed to send summary notification: {e}")

        return results

    def print_summary(self, results: List[SetupResult]):
        """Print summary of setup results"""
        total = len(results)
        created = sum(1 for r in results if r.status == 'created')
        exists = sum(1 for r in results if r.status == 'exists')
        errors = sum(1 for r in results if r.status == 'error')

        print("\n" + "="*60)
        print("SETUP SUMMARY")
        print("="*60)
        print(f"Total resources: {total}")
        print(f"Created:         {created}")
        print(f"Already exists:  {exists}")
        print(f"Errors:          {errors}")
        print("="*60)

        if errors > 0:
            print("\nErrors encountered:")
            for result in results:
                if result.status == 'error':
                    print(f"  - {result.resource_type}/{result.resource_name}: {result.message}")

        if exists > 0:
            print("\nResources that already exist:")
            for result in results:
                if result.status == 'exists':
                    print(f"  - {result.resource_type}/{result.resource_name}")


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
    script_dir = Path(__file__).parent.parent
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

    # Execute
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode, None
    except Exception:
        return 1, None


def send_setup_start_notification(config: Config) -> Tuple[int, Optional[str]]:
    """
    Send initial Slack notification when setup starts.

    Args:
        config: Configuration object

    Returns:
        Tuple of (exit_code, thread_ts)
    """
    title = "Nullplatform Setup Starting"

    resource_counts = {
        'namespace': 1 if config.namespace else 0,
        'applications': len(config.applications),
        'scopes': len(config.scopes),
        'parameters': len(config.parameters)
    }

    total_resources = sum(resource_counts.values())

    message_parts = [
        f"*Total Resources:* {total_resources}\n"
    ]

    if config.namespace:
        message_parts.append(f"*Namespace:* {config.namespace.get('name')}")

    message_parts.append(f"*Applications:* {len(config.applications)}")
    if config.applications:
        app_list = "\n".join([f"• {app.get('name')}" for app in config.applications[:5]])
        if len(config.applications) > 5:
            app_list += f"\n• ... and {len(config.applications) - 5} more"
        message_parts.append(app_list)

    message_parts.append(f"\n*Scopes:* {len(config.scopes)}")
    message_parts.append(f"*Parameters:* {len(config.parameters)}")

    message = "\n".join(message_parts)

    return send_slack_notification(
        title,
        message,
        status="info",
        template=str(Path(__file__).parent / "templates" / "nullplatform_setup_start.json"),
        template_vars={
            "TOTAL_RESOURCES": str(total_resources),
            "NAMESPACE_NAME": config.namespace.get('name') if config.namespace else "N/A",
            "APP_COUNT": str(len(config.applications)),
            "SCOPE_COUNT": str(len(config.scopes)),
            "PARAM_COUNT": str(len(config.parameters))
        }
    )


def send_resource_notification(
    result: SetupResult,
    thread_ts: Optional[str] = None
) -> int:
    """
    Send Slack notification for a single resource setup result.

    Args:
        result: SetupResult object
        thread_ts: Thread timestamp for threading (optional)

    Returns:
        Exit code
    """
    # Map status to notification status and icon
    status_map = {
        'created': ('success', ':white_check_mark:', 'Created'),
        'exists': ('info', ':information_source:', 'Already Exists'),
        'error': ('failure', ':x:', 'Error')
    }

    slack_status, icon, action = status_map.get(result.status, ('info', ':speech_balloon:', 'Processed'))

    title = f"{icon} {action}: {result.resource_type}/{result.resource_name}"

    message_parts = [
        f"*Resource Type:* {result.resource_type}",
        f"*Name:* {result.resource_name}",
        f"*Status:* {action}"
    ]

    if result.resource_id:
        message_parts.append(f"*ID:* `{result.resource_id}`")

    if result.message:
        message_parts.append(f"\n_{result.message}_")

    message = "\n".join(message_parts)

    exit_code, _ = send_slack_notification(
        title,
        message,
        status=slack_status,
        template=str(Path(__file__).parent / "templates" / "nullplatform_setup_progress.json"),
        template_vars={
            "RESOURCE_TYPE": result.resource_type,
            "RESOURCE_NAME": result.resource_name,
            "STATUS": action,
            "STATUS_ICON": icon,
            "RESOURCE_ID": result.resource_id or "N/A",
            "MESSAGE": result.message
        },
        thread_ts=thread_ts
    )

    return exit_code


def send_setup_summary_notification(
    config: Config,
    results: List[SetupResult],
    duration_seconds: Optional[float] = None,
    thread_ts: Optional[str] = None
) -> int:
    """
    Send Slack notification with setup summary.

    Args:
        config: Configuration object
        results: List of SetupResult objects
        duration_seconds: Duration of setup in seconds (optional)
        thread_ts: Thread timestamp for threading (optional)

    Returns:
        Exit code
    """
    total = len(results)
    created = sum(1 for r in results if r.status == 'created')
    exists = sum(1 for r in results if r.status == 'exists')
    errors = sum(1 for r in results if r.status == 'error')

    # Determine overall status
    if errors > 0:
        overall_status = 'failure'
        title = ":x: Nullplatform Setup Completed with Errors"
    elif created == 0 and exists > 0:
        overall_status = 'info'
        title = ":information_source: Nullplatform Setup Complete (All Resources Exist)"
    else:
        overall_status = 'success'
        title = ":white_check_mark: Nullplatform Setup Completed Successfully"

    # Build summary message
    message_parts = [
        "*Summary:*",
        f"• Total resources: {total}",
        f"• Created: {created}",
        f"• Already exist: {exists}",
        f"• Errors: {errors}"
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
                error_list.append(f"• {result.resource_type}/{result.resource_name}: {result.message}")
        if error_list:
            message_parts.append("\n*Errors:*")
            message_parts.extend(error_list[:10])
            if len(error_list) > 10:
                message_parts.append(f"• ... and {len(error_list) - 10} more errors")

    # Add breakdown by resource type
    by_type = {}
    for result in results:
        if result.resource_type not in by_type:
            by_type[result.resource_type] = {'created': 0, 'exists': 0, 'error': 0}
        by_type[result.resource_type][result.status] += 1

    if by_type:
        message_parts.append("\n*By Resource Type:*")
        for resource_type, counts in by_type.items():
            message_parts.append(
                f"• {resource_type}: {counts['created']} created, "
                f"{counts['exists']} existing, {counts['error']} errors"
            )

    message = "\n".join(message_parts)

    error_list_str = "\n".join([f"{r.resource_type}/{r.resource_name}: {r.message}"
                                for r in results if r.status == 'error'][:10])

    exit_code, _ = send_slack_notification(
        title,
        message,
        status=overall_status,
        template=str(Path(__file__).parent / "templates" / "nullplatform_setup_summary.json"),
        template_vars={
            "TOTAL": str(total),
            "CREATED": str(created),
            "EXISTS": str(exists),
            "ERRORS": str(errors),
            "DURATION": f"{int(duration_seconds // 60)}m {int(duration_seconds % 60)}s" if duration_seconds else "N/A",
            "ERROR_LIST": error_list_str if error_list_str else "None"
        },
        thread_ts=thread_ts
    )

    return exit_code


def main():
    parser = argparse.ArgumentParser(
        description='Setup nullplatform resources from config file',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --config nullplatform-setup.yaml
  %(prog)s --config nullplatform-setup.yaml --dry-run
  %(prog)s --config nullplatform-setup.yaml --verbose

Environment Variables:
  NULLPLATFORM_API_KEY    Nullplatform API key (if --api-key not provided)
        """
    )

    parser.add_argument(
        '--config',
        default='nullplatform-setup.yaml',
        help='Path to configuration file (default: nullplatform-setup.yaml)'
    )

    parser.add_argument(
        '--api-key',
        help='Nullplatform API key (or set NULLPLATFORM_API_KEY env var)'
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

    parser.add_argument(
        '--np-path',
        default='np',
        help='Path to np CLI binary (default: np)'
    )

    args = parser.parse_args()

    # Get API key
    api_key = args.api_key or os.environ.get('NULLPLATFORM_API_KEY')
    if not api_key and not args.dry_run:
        print("Error: API key required. Provide via --api-key or NULLPLATFORM_API_KEY env var")
        sys.exit(1)

    # Initialize setup handler
    setup = NullplatformSetup(
        api_key=api_key,
        dry_run=args.dry_run,
        verbose=args.verbose,
        np_path=args.np_path
    )

    # Load configuration
    config = setup.load_config(args.config)

    # Perform setup
    results = setup.setup_all(config)

    # Print summary
    setup.print_summary(results)

    # Exit with error code if any errors occurred
    errors = sum(1 for r in results if r.status == 'error')
    sys.exit(1 if errors > 0 else 0)


if __name__ == '__main__':
    main()
