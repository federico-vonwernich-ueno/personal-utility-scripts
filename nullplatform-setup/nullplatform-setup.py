#!/usr/bin/env python3
"""
Nullplatform Setup Script

Automates creation of nullplatform resources (applications, scopes, parameters)
from YAML configuration using the np CLI. Supports dry-run mode, automatic ID
tracking, and optional Slack notifications.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install it with: pip install PyYAML")
    sys.exit(1)


# Exit codes
EXIT_SUCCESS = 0
EXIT_ERROR = 1
SLACK_MISSING_DEPENDENCY = 2
SLACK_NO_TOKEN = 3
SLACK_NO_CHANNEL = 4

# Resource statuses
STATUS_CREATED = 'created'
STATUS_EXISTS = 'exists'
STATUS_ERROR = 'error'

# Resource types
RESOURCE_APPLICATION = 'application'
RESOURCE_PARAMETER = 'parameter'
RESOURCE_SCOPE = 'scope'
RESOURCE_NAMESPACE = 'namespace'

# Parameter types and defaults
PARAM_TYPE_ENVIRONMENT = 'environment'
PARAM_TYPE_FILE = 'file'
PARAM_ENCODING_PLAINTEXT = 'plaintext'
PARAM_ENCODING_BASE64 = 'base64'

# Valid scope request fields (per Nullplatform API schema)
# These are the ONLY fields that can be sent in the scope creation (POST) request
VALID_SCOPE_REQUEST_FIELDS = [
    'name',
    'type',
    'provider',
    'application_id',
    'requested_spec',
    'capabilities',
    'messages',
    'external_created'
]

# Valid scope update fields (per Nullplatform API schema)
# These fields can be set via PATCH /scope/:id after creation
# Note: dimensions has its own dedicated API (POST /scope/:id/dimension) and is NOT set via PATCH
VALID_SCOPE_UPDATE_FIELDS = [
    'status',
    'requested_spec',
    'tier',
    'capabilities',
    'asset_name',
    'messages',
    'instance_id',
    'domain',
    'name'
    # 'dimensions' is NOT included - it uses a separate API: POST /scope/:id/dimension
]

# Environment variables
ENV_NULLPLATFORM_API_KEY = 'NULLPLATFORM_API_KEY'
ENV_SLACK_DRY_RUN = 'SLACK_DRY_RUN'
ENV_SLACK_BOT_TOKEN = 'SLACK_BOT_TOKEN'
ENV_SLACK_CHANNEL = 'SLACK_CHANNEL'


@dataclass
class SetupResult:
    """Result of setting up a single resource"""
    resource_type: str  # 'application', 'parameter', 'scope', 'namespace'
    resource_name: str
    status: str  # 'created', 'exists', 'error'
    message: str
    resource_id: Optional[str] = None
    nrn: Optional[str] = None  # Nullplatform Resource Name


@dataclass
class Config:
    """Configuration for nullplatform setup"""
    organization_id: Optional[str]  # Nullplatform organization ID (required)
    account_id: Optional[str]  # Nullplatform account ID (required)
    applications: List[Dict]  # Each application contains nested scopes and parameters


class NullplatformSetup:
    """Handles nullplatform resource creation via np CLI"""

    def __init__(self, api_key: Optional[str] = None, dry_run: bool = False,
                 verbose: bool = False, np_path: str = "np"):
        self.api_key = api_key or os.environ.get(ENV_NULLPLATFORM_API_KEY)
        self.organization_id = None  # Set later from config in setup_all()
        self.account_id = None  # Set later from config in setup_all()
        self.dry_run = dry_run
        self.verbose = verbose
        self.np_path = np_path
        self.log_file_path = None  # Will be set by _setup_logger()
        self.logger = self._setup_logger()

        # Track created resource IDs for dependencies
        self.resource_ids = {
            'applications': {},  # name -> id
            'parameters': {},    # name -> id
            'scopes': {}         # name -> id
        }

        # Verify np command is available (skip in dry-run mode)
        if not self.dry_run:
            self._verify_np_command()

    def _verify_np_command(self):
        """Verify that the np CLI command is available and working"""
        try:
            result = subprocess.run(
                [self.np_path, '--version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                self.logger.warning(
                    f"Warning: '{self.np_path}' command may not be properly installed.\n"
                    f"Exit code: {result.returncode}\n"
                    f"Output: {result.stderr}"
                )
        except FileNotFoundError:
            self.logger.error(
                f"Error: '{self.np_path}' command not found.\n\n"
                f"The nullplatform CLI (np) is required but not found in your PATH.\n\n"
                f"Installation:\n"
                f"  curl https://cli.nullplatform.com/install.sh | sh\n\n"
                f"Or if installed elsewhere:\n"
                f"  python nullplatform-setup.py --np-path /path/to/np\n\n"
                f"Verify installation:\n"
                f"  np --version"
            )
            sys.exit(1)
        except subprocess.TimeoutExpired:
            self.logger.warning(
                f"Warning: '{self.np_path} --version' timed out after 5 seconds.\n"
                f"The command may be hung or extremely slow."
            )
        except Exception as e:
            self.logger.warning(
                f"Warning: Could not verify np command: {e}"
            )

    def _setup_logger(self) -> logging.Logger:
        """Configure logging to console and file"""
        logger = logging.getLogger('nullplatform-setup')
        level = logging.DEBUG if self.verbose else logging.INFO
        logger.setLevel(level)

        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # File handler with timestamped filename
        timestamp = datetime.now().strftime('%Y-%m-%d-%H%M%S')
        log_filename = f'nullplatform-setup-{timestamp}.log'
        self.log_file_path = log_filename

        file_handler = logging.FileHandler(log_filename, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        logger.info(f"Logging to file: {log_filename}")

        return logger

    def _run_np_command(self, command: List[str], json_body: Optional[Dict] = None,
                        account_id: Optional[str] = None) -> Tuple[int, str, str]:
        """
        Run an np CLI command and return (returncode, stdout, stderr)

        Args:
            command: Command parts (e.g., ['application', 'create'])
            json_body: Optional JSON body to pass via --body
            account_id: Optional account ID to pass via --account_id
        """
        cmd = [self.np_path] + command

        # Add account ID if provided
        if account_id:
            cmd.extend(['--account_id', account_id])

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
            # Log error details at ERROR level for better visibility
            self.logger.error(f"Command failed with exit code {result.returncode}")

            # Show command with redacted API key
            safe_cmd = [self.np_path] + command + ['--format', 'json']
            if self.api_key:
                safe_cmd.extend(['--api-key', '[REDACTED]'])
            self.logger.error(f"Command: {' '.join(safe_cmd)}")

            # Show full output (truncate if extremely long)
            stdout_preview = result.stdout[:500] if len(result.stdout) > 500 else result.stdout
            stderr_preview = result.stderr[:500] if len(result.stderr) > 500 else result.stderr
            self.logger.error(f"stdout: {stdout_preview}")
            self.logger.error(f"stderr: {stderr_preview}")

            # Provide diagnostic hint
            hint = self._diagnose_error(result.returncode, result.stdout, result.stderr)
            self.logger.error(f"\nDiagnostic hint:\n{hint}")

        return result.returncode, result.stdout, result.stderr

    def _diagnose_error(self, returncode: int, stdout: str, stderr: str) -> str:
        """
        Analyze error patterns and provide helpful diagnostic hints.

        Args:
            returncode: Exit code from command
            stdout: Standard output
            stderr: Standard error

        Returns:
            User-friendly diagnostic hint
        """
        combined_output = f"{stdout} {stderr}".lower()

        # Check for authentication errors
        if any(word in combined_output for word in ['unauthorized', 'authentication', 'invalid api key', 'invalid_api_key']):
            return (
                "Authentication failed. Check your API key (NULLPLATFORM_API_KEY).\n"
                "  1. Verify your API key: echo $NULLPLATFORM_API_KEY\n"
                "  2. Check if the key has expired in nullplatform UI\n"
                "  3. Ensure you have permission to perform this operation"
            )

        # Check for network errors
        if any(word in combined_output for word in ['connection refused', 'could not resolve', 'timeout', 'network', 'unreachable']):
            return (
                "Network error. Check your internet connection and API endpoint.\n"
                "  1. Verify you can reach the internet\n"
                "  2. Check if a proxy is required\n"
                "  3. Verify DNS resolution works"
            )

        # Check for HTML error page (API down or wrong endpoint)
        if stdout.strip().startswith('<') or '<html' in stdout.lower() or '<!doctype' in stdout.lower():
            return (
                "Received HTML response (possibly error page). API might be down or endpoint incorrect.\n"
                "  1. Check nullplatform status page\n"
                "  2. Verify your np CLI is up to date: np --version\n"
                "  3. Try again in a few minutes"
            )

        # Check for rate limiting
        if '429' in combined_output or 'rate limit' in combined_output or 'too many requests' in combined_output:
            return (
                "Rate limited. You've made too many requests.\n"
                "  1. Wait a few minutes before retrying\n"
                "  2. Consider adding delays between operations"
            )

        # Check for permission errors
        if any(word in combined_output for word in ['permission denied', 'forbidden', 'access denied', 'insufficient permissions']):
            return (
                "Permission denied. Your API key may lack necessary permissions.\n"
                "  1. Check your role/permissions in nullplatform UI\n"
                "  2. Verify the API key has the required scopes\n"
                "  3. Contact your nullplatform administrator"
            )

        # Check for not found errors
        if '404' in combined_output or 'not found' in combined_output:
            return (
                "Resource not found.\n"
                "  1. Verify the resource name/ID is correct\n"
                "  2. Check if the resource exists: np <resource> list\n"
                "  3. Ensure you're using the correct namespace"
            )

        # Generic error
        return (
            "Unknown error. Check command output above for details.\n"
            "  1. Run with --verbose for more information\n"
            "  2. Check nullplatform documentation\n"
            "  3. Verify your np CLI is up to date: np --version"
        )

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

        # In dry-run mode, return a mock namespace ID
        if self.dry_run:
            mock_id = f"ns-{namespace_name}-dryrun"
            self.logger.debug(f"[DRY RUN] Using mock namespace ID: {mock_id}")
            return mock_id

        returncode, stdout, stderr = self._run_np_command(['namespace', 'list'], account_id=self.account_id)

        if returncode != 0:
            # Error details already logged by _run_np_command
            raise ValueError(
                f"Failed to list namespaces (exit code {returncode}). "
                f"See error details above."
            )

        # Show raw response if verbose (helps debug JSON parsing issues)
        if self.verbose and stdout:
            self.logger.debug(f"Raw namespace list response: {stdout[:]}")

        try:
            response = json.loads(stdout)
            # Extract namespaces from paginated response structure
            namespaces = response.get('results', []) if isinstance(response, dict) else response
            self.logger.debug(f"Found {len(namespaces)} namespace(s)")
        except json.JSONDecodeError as e:
            # Show what we tried to parse to help debugging
            stdout_preview = stdout[:500] if len(stdout) > 500 else stdout
            raise ValueError(
                f"Failed to parse namespace list response as JSON.\n"
                f"JSON parse error: {str(e)}\n"
                f"Raw response (first 500 chars): {stdout_preview}\n\n"
                f"This usually means:\n"
                f"  - API returned an error page (HTML) instead of JSON\n"
                f"  - Network issue caused incomplete response\n"
                f"  - API endpoint is incorrect or unavailable\n\n"
                f"Troubleshooting:\n"
                f"  1. Check if np CLI is working: np --version\n"
                f"  2. Verify API key is set: echo $NULLPLATFORM_API_KEY\n"
                f"  3. Try listing namespaces directly: np namespace list"
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

    def _build_application_nrn(self, namespace_id: str, application_id: str) -> str:
        """
        Build an application NRN string from component IDs.

        Args:
            namespace_id: Namespace ID
            application_id: Application ID

        Returns:
            NRN string in format: organization=X:account=Y:namespace=Z:application=W
        """
        nrn = f"organization={self.organization_id}:account={self.account_id}:namespace={namespace_id}:application={application_id}"
        self.logger.debug(f"Built application NRN: {nrn}")
        return nrn

    def _build_scope_nrn(self, namespace_id: str, application_id: str, scope_id: str) -> str:
        """
        Build a scope NRN string from component IDs.

        Args:
            namespace_id: Namespace ID
            application_id: Application ID
            scope_id: Scope ID

        Returns:
            NRN string in format: organization=X:account=Y:namespace=Z:application=W:scope=V
        """
        nrn = f"organization={self.organization_id}:account={self.account_id}:namespace={namespace_id}:application={application_id}:scope={scope_id}"
        self.logger.debug(f"Built scope NRN: {nrn}")
        return nrn

    def _handle_api_response(self, resource_type: str, resource_name: str,
                            returncode: int, stdout: str, stderr: str,
                            resource_dict_key: str = None, nrn: Optional[str] = None) -> SetupResult:
        """
        Handle API response and return SetupResult.
        Consolidates common response handling logic across all create_* methods.

        Args:
            resource_type: Type of resource ('application', 'parameter', 'scope')
            resource_name: Name of the resource
            returncode: Command return code
            stdout: Command stdout
            stderr: Command stderr
            resource_dict_key: Optional key for self.resource_ids dict (defaults to resource_type + 's')
            nrn: Optional Nullplatform Resource Name

        Returns:
            SetupResult object
        """
        if resource_dict_key is None:
            resource_dict_key = resource_type + 's'

        if returncode == 0:
            try:
                response = json.loads(stdout)
                resource_id = response.get('id')

                if resource_dict_key in self.resource_ids:
                    self.resource_ids[resource_dict_key][resource_name] = resource_id

                # Log with NRN if available
                if nrn:
                    self.logger.info(f"✓ Created {resource_type}: {resource_name} (ID: {resource_id}, NRN: {nrn})")
                else:
                    self.logger.info(f"✓ Created {resource_type}: {resource_name} (ID: {resource_id})")

                return SetupResult(
                    resource_type=resource_type,
                    resource_name=resource_name,
                    status=STATUS_CREATED,
                    message=f'{resource_type.capitalize()} created successfully',
                    resource_id=resource_id,
                    nrn=nrn
                )
            except json.JSONDecodeError:
                self.logger.error(f"Failed to parse response: {stdout}")
                return SetupResult(
                    resource_type=resource_type,
                    resource_name=resource_name,
                    status=STATUS_ERROR,
                    message=f'Failed to parse response: {stdout}'
                )
        else:
            # Command failed
            self.logger.error(f"Failed to create {resource_type}: {stderr}")
            return SetupResult(
                resource_type=resource_type,
                resource_name=resource_name,
                status=STATUS_ERROR,
                message=f'Error: {stderr}'
            )

    def _lookup_existing_resource(self, resource_type: str, resource_name: str) -> Optional[str]:
        """
        Look up existing resource ID by name.
        Consolidates duplicate lookup logic for "already exists" scenarios.

        Args:
            resource_type: Type of resource ('application', 'scope', etc.)
            resource_name: Name of the resource to find

        Returns:
            Resource ID if found, None otherwise
        """
        list_command = [resource_type, 'list']
        returncode, stdout, stderr = self._run_np_command(list_command, account_id=self.account_id)

        if returncode == 0:
            try:
                response = json.loads(stdout)
                # Extract resources from paginated response structure
                resources = response.get('results', []) if isinstance(response, dict) else response

                for resource in resources:
                    if resource.get('name') == resource_name:
                        resource_id = resource.get('id')
                        self.logger.debug(f"Found existing {resource_type} '{resource_name}' with ID: {resource_id}")
                        return resource_id
            except Exception as e:
                self.logger.debug(f"Failed to lookup existing {resource_type}: {e}")

        return None

    def _handle_already_exists(self, resource_type: str, resource_name: str) -> SetupResult:
        """
        Handle 'already exists' scenario for resources.

        Args:
            resource_type: Type of resource ('application', 'scope', 'parameter')
            resource_name: Name of the resource

        Returns:
            SetupResult with 'exists' status
        """
        self.logger.warning(f"{resource_type.capitalize()} {resource_name} already exists")

        # Try to get existing resource ID
        existing_id = self._lookup_existing_resource(resource_type, resource_name)
        resource_dict_key = resource_type + 's'

        if existing_id and resource_dict_key in self.resource_ids:
            self.resource_ids[resource_dict_key][resource_name] = existing_id

        return SetupResult(
            resource_type=resource_type,
            resource_name=resource_name,
            status=STATUS_EXISTS,
            message=f'{resource_type.capitalize()} already exists'
        )

    def _create_parameter_value(self, param_name: str, param_id: str, param_config: Dict) -> Tuple[bool, str]:
        """
        Create a value for a parameter.

        Args:
            param_name: Name of the parameter
            param_id: ID of the parameter
            param_config: Parameter configuration dict (must include 'value', optionally 'dimensions')

        Returns:
            Tuple of (success: bool, message: str)
        """
        value_config = {
            'value': param_config['value']
        }

        # Add dimensions if present
        if 'dimensions' in param_config:
            value_config['dimensions'] = param_config['dimensions']
            self.logger.debug(f"Setting parameter value with dimensions: {param_config['dimensions']}")

            # Validate: Dimensions require application-level NRN
            if 'scope_id' in param_config:
                self.logger.warning(
                    f"Parameter '{param_name}' has dimensions but uses scope-level NRN. "
                    f"Nullplatform API requires application-level NRN for dimensions. "
                    f"This may fail."
                )

        # Build NRN for the parameter value
        if 'scope_id' in param_config:
            # Scope-level NRN
            value_config['nrn'] = self._build_scope_nrn(
                str(param_config['namespace_id']),
                str(param_config['application_id']),
                str(param_config['scope_id'])
            )
            self.logger.debug(f"Using scope-level NRN for parameter value: {param_name}")
        else:
            # Application-level NRN
            value_config['nrn'] = self._build_application_nrn(
                str(param_config['namespace_id']),
                str(param_config['application_id'])
            )
            self.logger.debug(f"Using application-level NRN for parameter value: {param_name}")

        returncode, stdout, stderr = self._run_np_command(
            ['parameter', 'value', 'create', '--id', str(param_id)],
            json_body=value_config
        )

        if returncode == 0:
            self.logger.info(f"✓ Set value for parameter: {param_name}")
            return True, 'Parameter created and value set successfully'
        else:
            self.logger.warning(f"Created parameter but failed to set value: {stderr}")
            return False, f'Parameter created but value not set: {stderr}'

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
            organization_id=data.get('organization_id'),
            account_id=data.get('account_id'),
            applications=data.get('applications', [])
        )

        # Validate organization_id is provided
        if not config.organization_id:
            self.logger.error(
                "Error: 'organization_id' is required in configuration file.\n\n"
                "Add to your config file:\n"
                "  organization_id: \"your-organization-id\"\n\n"
                "Get your organization ID with:\n"
                "  np organization list --format json"
            )
            sys.exit(1)

        # Validate account_id is provided
        if not config.account_id:
            self.logger.error(
                "Error: 'account_id' is required in configuration file.\n\n"
                "Add to your config file:\n"
                "  account_id: \"your-account-id\"\n\n"
                "Get your account ID with:\n"
                "  np account list --format json"
            )
            sys.exit(1)

        self.logger.info(f"Config loaded: organization_id={config.organization_id}, account_id={config.account_id}, {len(config.applications)} applications")

        return config

    def create_application(self, app_config: Dict) -> SetupResult:
        """Create an application"""
        name = app_config.get('name')

        self.logger.info(f"Creating application: {name}")

        # Filter fields to only those expected by the application create API
        # Exclude nested resources (scopes, parameters) and already-processed fields (namespace)
        excluded_fields = ['scopes', 'parameters', 'namespace', 'repository']
        api_config = {k: v for k, v in app_config.items() if k not in excluded_fields}

        # Transform nested repository structure to flat repository_url
        if 'repository' in app_config:
            if isinstance(app_config['repository'], dict) and 'url' in app_config['repository']:
                api_config['repository_url'] = app_config['repository']['url']
                self.logger.debug(f"Transformed repository.url to repository_url for {name}")
            elif isinstance(app_config['repository'], str):
                # Support simplified format: repository: "url"
                api_config['repository_url'] = app_config['repository']
                self.logger.debug(f"Using repository as repository_url for {name}")

        # Validate repository_url is present (required by API)
        if 'repository_url' not in api_config or not api_config.get('repository_url'):
            self.logger.error(
                f"Application '{name}' is missing repository_url.\n\n"
                f"Add to your application config:\n"
                f"  repository_url: \"https://github.com/your-org/your-repo\"\n\n"
                f"Or use nested format:\n"
                f"  repository:\n"
                f"    url: \"https://github.com/your-org/your-repo\""
            )
            return SetupResult(
                resource_type=RESOURCE_APPLICATION,
                resource_name=name,
                status=STATUS_ERROR,
                message='Missing required field: repository_url'
            )

        returncode, stdout, stderr = self._run_np_command(
            ['application', 'create'],
            json_body=api_config
        )

        # Check if application already exists
        if returncode != 0 and 'already exists' in stderr.lower():
            return self._handle_already_exists(RESOURCE_APPLICATION, name)

        # Build NRN if creation was successful
        nrn = None
        if returncode == 0 and 'namespace_id' in app_config:
            try:
                response = json.loads(stdout)
                app_id = response.get('id')
                if app_id:
                    nrn = self._build_application_nrn(str(app_config['namespace_id']), str(app_id))
            except json.JSONDecodeError:
                pass  # Will be handled by _handle_api_response

        # Handle API response (success or other errors)
        return self._handle_api_response(RESOURCE_APPLICATION, name, returncode, stdout, stderr, nrn=nrn)

    def create_parameter(self, param_config: Dict) -> SetupResult:
        """
        Create a parameter and optionally set its value(s).

        Supports two modes:
        1. Single value: param_config contains 'value' field
        2. Multiple values: param_config contains 'values' array, each with its own scope/dimensions
        """
        name = param_config.get('name')

        self.logger.info(f"Creating parameter: {name}")

        # Build the parameter definition with required API fields
        # Remove fields that are not part of the API schema
        param_def = {k: v for k, v in param_config.items() if k not in ['value', 'values', 'scope', 'application_id', 'namespace_id', 'scope_id']}

        # Build NRN from application_id and namespace_id
        param_nrn = None
        if 'application_id' in param_config and 'namespace_id' in param_config:
            param_nrn = self._build_application_nrn(
                str(param_config['namespace_id']),
                str(param_config['application_id'])
            )
            param_def['nrn'] = param_nrn
        else:
            self.logger.error(f"Missing application_id or namespace_id for parameter {name}")
            return SetupResult(
                resource_type=RESOURCE_PARAMETER,
                resource_name=name,
                status=STATUS_ERROR,
                message='Missing application_id or namespace_id'
            )

        # Set default values for required API fields if not provided
        if 'type' not in param_def:
            param_def['type'] = PARAM_TYPE_ENVIRONMENT
            self.logger.debug(f"Setting default type={PARAM_TYPE_ENVIRONMENT} for parameter {name}")

        if 'encoding' not in param_def:
            param_def['encoding'] = PARAM_ENCODING_PLAINTEXT
            self.logger.debug(f"Setting default encoding={PARAM_ENCODING_PLAINTEXT} for parameter {name}")

        if 'secret' not in param_def:
            param_def['secret'] = False
            self.logger.debug(f"Setting default secret=false for parameter {name}")

        if 'read_only' not in param_def:
            param_def['read_only'] = False
            self.logger.debug(f"Setting default read_only=false for parameter {name}")

        # Set variable name for environment type parameters
        if param_def['type'] == PARAM_TYPE_ENVIRONMENT and 'variable' not in param_def:
            param_def['variable'] = name
            self.logger.debug(f"Setting default variable={name} for environment parameter {name}")

        # Validate required conditional fields
        if param_def['type'] == PARAM_TYPE_FILE and 'destination_path' not in param_def:
            self.logger.error(f"Parameter {name} has type=file but missing destination_path")
            return SetupResult(
                resource_type=RESOURCE_PARAMETER,
                resource_name=name,
                status=STATUS_ERROR,
                message='File type parameters require destination_path'
            )

        returncode, stdout, stderr = self._run_np_command(
            ['parameter', 'create'],
            json_body=param_def
        )

        # Check if parameter already exists
        if returncode != 0 and 'already exists' in stderr.lower():
            return self._handle_already_exists(RESOURCE_PARAMETER, name)

        # Handle creation success/failure
        result = self._handle_api_response(RESOURCE_PARAMETER, name, returncode, stdout, stderr, nrn=param_nrn)

        # If parameter created successfully, set value(s)
        if result.status == STATUS_CREATED:
            if 'values' in param_config:
                # Multiple values: loop through and create each one
                values_list = param_config['values']
                if not isinstance(values_list, list):
                    self.logger.error(f"Parameter '{name}' has 'values' field but it's not a list")
                    result.message = "Invalid 'values' field: must be a list"
                    result.status = STATUS_ERROR
                    return result

                self.logger.info(f"Creating {len(values_list)} value(s) for parameter: {name}")
                success_count = 0
                messages = []

                for i, value_config in enumerate(values_list):
                    # Build context for this value
                    value_context = {
                        'value': value_config.get('value'),
                        'application_id': param_config.get('application_id'),
                        'namespace_id': param_config.get('namespace_id')
                    }

                    # Add scope if specified
                    if 'scope' in value_config:
                        scope_name = value_config['scope']
                        scope_id = self.resource_ids['scopes'].get(scope_name)
                        if scope_id:
                            value_context['scope_id'] = scope_id
                        else:
                            self.logger.warning(f"Scope '{scope_name}' not found for parameter value #{i+1}")

                    # Add dimensions if specified
                    if 'dimensions' in value_config:
                        value_context['dimensions'] = value_config['dimensions']

                    # Create the value
                    success, message = self._create_parameter_value(name, result.resource_id, value_context)
                    messages.append(f"Value #{i+1}: {message}")
                    if success:
                        success_count += 1

                # Update result message
                result.message = f"Parameter created with {success_count}/{len(values_list)} values set. " + "; ".join(messages)

            elif 'value' in param_config:
                # Single value: use existing logic
                success, message = self._create_parameter_value(name, result.resource_id, param_config)
                result.message = message

        return result

    def create_scope(self, scope_config: Dict) -> SetupResult:
        """Create a scope"""
        name = scope_config.get('name')

        self.logger.info(f"Creating scope: {name}")

        # Filter to only valid API request fields
        # Fields like 'namespace_id', 'dimensions', 'visibility' are not part of the creation request
        scope_def = {k: v for k, v in scope_config.items() if k in VALID_SCOPE_REQUEST_FIELDS}

        # Log filtered fields for debugging
        filtered_fields = [k for k in scope_config.keys() if k not in VALID_SCOPE_REQUEST_FIELDS]
        if filtered_fields:
            self.logger.debug(f"Filtered out non-API fields for scope '{name}': {', '.join(filtered_fields)}")

        returncode, stdout, stderr = self._run_np_command(
            ['scope', 'create'],
            json_body=scope_def
        )

        # Check if scope already exists
        if returncode != 0 and 'already exists' in stderr.lower():
            return self._handle_already_exists(RESOURCE_SCOPE, name)

        # Build NRN and handle post-creation updates if creation was successful
        nrn = None
        scope_id = None
        if returncode == 0 and 'namespace_id' in scope_config and 'application_id' in scope_config:
            try:
                response = json.loads(stdout)
                scope_id = response.get('id')
                if scope_id:
                    nrn = self._build_scope_nrn(
                        str(scope_config['namespace_id']),
                        str(scope_config['application_id']),
                        str(scope_id)
                    )
            except json.JSONDecodeError:
                pass  # Will be handled by _handle_api_response

        # Handle API response (success or other errors)
        result = self._handle_api_response(RESOURCE_SCOPE, name, returncode, stdout, stderr, nrn=nrn)

        # Step 2: If creation succeeded and there are dimensions, assign them
        if result.status == STATUS_CREATED and scope_id:
            if 'dimensions' in scope_config:
                dimensions = scope_config['dimensions']
                self.logger.debug(f"Scope '{name}' has dimensions to assign: {dimensions}")
                dim_result = self.assign_scope_dimensions(scope_id, name, dimensions)

                # If dimension assignment failed, log warning but don't fail overall (scope was created)
                if dim_result.status == STATUS_ERROR:
                    self.logger.warning(f"Scope '{name}' created but dimension assignment failed: {dim_result.message}")
                    result.message += f" (Warning: Dimension assignment failed: {dim_result.message})"

        return result

    def assign_scope_dimensions(self, scope_id: str, scope_name: str, dimensions: Dict) -> SetupResult:
        """
        Assign dimensions to a scope using the dedicated dimension API.
        Uses POST /scope/:scopeId/dimension endpoint.

        Dimensions cannot be set via PATCH - they require a separate API call.
        """
        self.logger.info(f"Assigning dimensions to scope: {scope_name} ({dimensions})")

        returncode, stdout, stderr = self._run_np_command(
            ['scope', 'dimension', 'create', '--scope-id', str(scope_id)],
            json_body=dimensions
        )

        # Check for success (dimension API returns 204 No Content on success)
        if returncode == 0:
            self.logger.info(f"✓ Assigned dimensions to scope: {scope_name}")
            return SetupResult(
                resource_type=RESOURCE_SCOPE,
                resource_name=scope_name,
                status=STATUS_CREATED,  # Use CREATED status since this is part of creation flow
                message=f'Scope dimensions assigned successfully',
                resource_id=scope_id
            )
        else:
            self.logger.error(f"Failed to assign dimensions to scope: {scope_name}")
            self.logger.error(f"Error: {stderr}")
            if stdout:
                self.logger.error(f"API response: {stdout}")
            return SetupResult(
                resource_type=RESOURCE_SCOPE,
                resource_name=scope_name,
                status=STATUS_ERROR,
                message=f'Failed to assign dimensions: {stderr}',
                resource_id=scope_id
            )

    def setup_all(self, config: Config) -> List[SetupResult]:
        """
        Setup all resources from config with nested structure.
        Each application contains its own scopes and parameters.
        Returns list of SetupResult objects.
        """
        import time

        self.organization_id = config.organization_id
        self.account_id = config.account_id

        results = []
        start_time = time.time()

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
                        resource_type=RESOURCE_APPLICATION,
                        resource_name=app_name,
                        status=STATUS_ERROR,
                        message=str(e)
                    )
                    results.append(result)
                    continue

            # 2. Create application
            app_result = self.create_application(app_config)
            results.append(app_result)

            if app_result.status == STATUS_ERROR:
                self.logger.error(f"Failed to create application {app_name}, skipping its scopes and parameters")
                continue

            app_id = app_result.resource_id

            # 3. Create scopes for this application
            scopes = app_config.get('scopes', [])
            for scope_config in scopes:
                scope_config['application_id'] = app_id
                scope_config['namespace_id'] = app_config.get('namespace_id')
                scope_result = self.create_scope(scope_config)
                results.append(scope_result)

            # 4. Create parameters for this application
            parameters = app_config.get('parameters', [])
            for param_config in parameters:
                param_config['application_id'] = app_id
                param_config['namespace_id'] = app_config.get('namespace_id')

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

        duration = time.time() - start_time

        try:
            slack_rc = send_setup_summary_notification(config, results, duration, thread_ts=None, setup=self)
            if slack_rc == EXIT_SUCCESS:
                self.logger.debug("[SLACK] Summary notification sent successfully")
            elif slack_rc not in (SLACK_MISSING_DEPENDENCY, SLACK_NO_TOKEN, SLACK_NO_CHANNEL):
                self.logger.debug(f"[SLACK] Summary notification failed with code {slack_rc}")
        except Exception as e:
            self.logger.debug(f"[SLACK] Failed to send summary notification: {e}")

        return results

    def print_summary(self, results: List[SetupResult]):
        """Print summary of setup results"""
        stats = _calculate_setup_statistics(results)
        total = stats['total']
        created = stats['created']
        exists = stats['exists']
        errors = stats['errors']

        print("\n" + "="*60)
        print("SETUP SUMMARY")
        print("="*60)
        print(f"Total resources: {total}")
        print(f"Created:         {created}")
        print(f"Already exists:  {exists}")
        print(f"Errors:          {errors}")
        print("="*60)

        # Show created resources with IDs
        if created > 0:
            created_output = _format_created_resources(results)
            if created_output:
                print("\nCreated Resources:")
                print(created_output)

        if errors > 0:
            print("\nErrors encountered:")
            for result in results:
                if result.status == STATUS_ERROR:
                    print(f"  - {result.resource_type}/{result.resource_name}: {result.message}")

        if exists > 0:
            print("\nResources that already exist:")
            for result in results:
                if result.status == STATUS_EXISTS:
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
    dry_run_flag = os.environ.get(ENV_SLACK_DRY_RUN)
    token = os.environ.get(ENV_SLACK_BOT_TOKEN)
    channel = os.environ.get(ENV_SLACK_CHANNEL)

    if not token and not dry_run_flag:
        return SLACK_NO_TOKEN

    if not channel and not dry_run_flag:
        return SLACK_NO_CHANNEL

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
        dry_run_flag = os.environ.get(ENV_SLACK_DRY_RUN)

        if dry_run_flag:
            return None
        else:
            return SLACK_MISSING_DEPENDENCY


def send_slack_notification(
    title: str,
    message: str = "",
    status: str = "info",
    template: Optional[str] = None,
    template_vars: Optional[Dict[str, str]] = None,
    thread_ts: Optional[str] = None,
    files: Optional[List[str]] = None
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
        files: List of file paths to upload (optional)

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
        return SLACK_MISSING_DEPENDENCY, None

    # Check dependencies
    dep_error = check_slack_dependencies()
    if dep_error:
        return dep_error, None

    # Build command
    dry_run_flag = bool(os.environ.get(ENV_SLACK_DRY_RUN))
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

    if files:
        cmd.append("--files")
        cmd.extend(files)

    # Execute
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode, None
    except Exception:
        return EXIT_ERROR, None


def _calculate_setup_statistics(results: List[SetupResult]) -> Dict:
    """
    Calculate statistics from setup results.

    Args:
        results: List of SetupResult objects

    Returns:
        Dict with keys: total, created, exists, errors
    """
    return {
        'total': len(results),
        'created': sum(1 for r in results if r.status == STATUS_CREATED),
        'exists': sum(1 for r in results if r.status == STATUS_EXISTS),
        'errors': sum(1 for r in results if r.status == STATUS_ERROR)
    }


def _format_created_resources(results: List[SetupResult]) -> str:
    """
    Format created resources grouped by type with IDs and NRNs.

    Args:
        results: List of SetupResult objects

    Returns:
        Formatted multi-line string showing created resources with IDs and NRNs
    """
    # Group created resources by type
    by_type = {
        RESOURCE_APPLICATION: [],
        RESOURCE_SCOPE: [],
        RESOURCE_PARAMETER: []
    }

    for result in results:
        if result.status == STATUS_CREATED and result.resource_id:
            if result.resource_type in by_type:
                by_type[result.resource_type].append(result)

    # Build formatted output
    lines = []

    # Applications
    if by_type[RESOURCE_APPLICATION]:
        lines.append("\nApplications:")
        for result in by_type[RESOURCE_APPLICATION]:
            lines.append(f"  ✓ {result.resource_name}")
            lines.append(f"    ID: {result.resource_id}")
            if result.nrn:
                lines.append(f"    NRN: {result.nrn}")

    # Scopes
    if by_type[RESOURCE_SCOPE]:
        lines.append("\nScopes:")
        for result in by_type[RESOURCE_SCOPE]:
            lines.append(f"  ✓ {result.resource_name}")
            lines.append(f"    ID: {result.resource_id}")
            if result.nrn:
                lines.append(f"    NRN: {result.nrn}")

    # Parameters
    if by_type[RESOURCE_PARAMETER]:
        lines.append("\nParameters:")
        for result in by_type[RESOURCE_PARAMETER]:
            lines.append(f"  ✓ {result.resource_name}")
            lines.append(f"    ID: {result.resource_id}")
            if result.nrn:
                lines.append(f"    NRN: {result.nrn}")

    return "\n".join(lines) if lines else ""


def send_setup_summary_notification(
    config: Config,
    results: List[SetupResult],
    duration_seconds: Optional[float] = None,
    thread_ts: Optional[str] = None,
    setup: Optional['NullplatformSetup'] = None
) -> int:
    """
    Send Slack notification with setup summary and log file.

    Args:
        config: Configuration object
        results: List of SetupResult objects
        duration_seconds: Duration of setup in seconds (optional)
        thread_ts: Thread timestamp for threading (optional)
        setup: NullplatformSetup instance for accessing log file path (optional)

    Returns:
        Exit code
    """
    stats = _calculate_setup_statistics(results)
    total = stats['total']
    created = stats['created']
    exists = stats['exists']
    errors = stats['errors']

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

    # Add created resources with IDs
    created_resources_str = ""
    if created > 0:
        created_output = _format_created_resources(results)
        if created_output:
            created_resources_str = created_output
            message_parts.append("\n*Created Resources:*")
            # Convert to Slack format (already has proper structure)
            for line in created_output.split('\n'):
                if line.strip():
                    message_parts.append(line)

    # Add error details
    if errors > 0:
        error_list = []
        for result in results:
            if result.status == STATUS_ERROR:
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
            by_type[result.resource_type] = {STATUS_CREATED: 0, STATUS_EXISTS: 0, STATUS_ERROR: 0}
        by_type[result.resource_type][result.status] += 1

    if by_type:
        message_parts.append("\n*By Resource Type:*")
        for resource_type, counts in by_type.items():
            message_parts.append(
                f"• {resource_type}: {counts[STATUS_CREATED]} created, "
                f"{counts[STATUS_EXISTS]} existing, {counts[STATUS_ERROR]} errors"
            )

    message = "\n".join(message_parts)

    error_list_str = "\n".join([f"{r.resource_type}/{r.resource_name}: {r.message}"
                                for r in results if r.status == STATUS_ERROR][:10])

    # Prepare log file for upload if available
    log_files = []
    if setup and setup.log_file_path:
        log_files = [setup.log_file_path]

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
            "ERROR_LIST": error_list_str if error_list_str else "None",
            "CREATED_RESOURCES": created_resources_str if created_resources_str else "None"
        },
        thread_ts=thread_ts,
        files=log_files if log_files else None
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
    api_key = args.api_key or os.environ.get(ENV_NULLPLATFORM_API_KEY)
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
    errors = sum(1 for r in results if r.status == STATUS_ERROR)
    sys.exit(EXIT_ERROR if errors > 0 else EXIT_SUCCESS)


if __name__ == '__main__':
    main()
