"""Branding constants for the package.

This file contains all user-facing strings that differ between the sandbox
and console variants of this package. To create a different branded version,
only this file (plus pyproject.toml and README.md) needs to change.

On the main branch: PRODUCT_NAME = "sandbox" (do-app-sandbox package)
On the console branch: PRODUCT_NAME = "console" (do-app-console package)
"""

# =============================================================================
# CORE BRANDING - Change these for different package variants
# =============================================================================

PRODUCT_NAME = "console"
PRODUCT_NAME_PLURAL = "apps"
CLI_COMMAND = "console"

# Package description (shown in --version and help)
PRODUCT_DESCRIPTION = "Remote console for DigitalOcean App Platform apps"
PRODUCT_VERSION = "0.1.2"

# =============================================================================
# CLI HELP TEXT
# =============================================================================

HELP_MAIN = "Remote console for DigitalOcean App Platform apps"
HELP_SETUP = "Build and push images to your DOCR registry"
HELP_CREATE = "Create a new temporary environment"
HELP_LIST = "List all managed apps"
HELP_DELETE = "Delete a managed app"
HELP_EXEC = "Execute a command in an app"
HELP_IMAGE = "Manage custom images"

# Argument help
HELP_ARG_NAME = "Name for the environment (auto-generated if not provided)"
HELP_ARG_TARGET = "App name or ID"
HELP_ARG_DELETE_NAME = "App name to delete"

# =============================================================================
# USER-FACING MESSAGES
# =============================================================================

# Create messages
MSG_CREATING = "Creating environment..."
MSG_CREATED = "Environment created successfully!"
MSG_CREATE_ERROR = "Error creating environment: {error}"

# List messages
MSG_LIST_EMPTY = "No apps found."
MSG_LIST_ERROR = "Error listing apps: {error}"

# Delete messages
MSG_DELETE_CONFIRM_ALL = "Are you sure you want to delete ALL managed apps? [y/N]: "
MSG_DELETE_NONE = "No apps to delete."
MSG_DELETED = "Deleted: {name} ({app_id})"
MSG_DELETE_FAILED = "Failed to delete {name}: {error}"
MSG_DELETE_COUNT = "Deleted {count} app(s)."
MSG_DELETE_SUCCESS = "Deleted app: {target}"
MSG_DELETE_ERROR = "Error deleting app: {error}"
MSG_NOT_FOUND = "App '{name}' not found."

# Exec messages
MSG_EXEC_ERROR = "Error executing command: {error}"

# Status messages (for --no-wait)
MSG_STILL_DEPLOYING = "Note: App may still be deploying. Use '{cmd} list' to check status."

# =============================================================================
# DOCSTRINGS (for module-level documentation)
# =============================================================================

CLI_MODULE_DOC = """Command-line interface for DO App Console.

This module provides a CLI for managing and troubleshooting DigitalOcean App Platform apps.

Usage:
    {cmd} exec --id APP_ID "command"      # Run command in existing app
    {cmd} create --image python           # Create a new environment
    {cmd} list                            # List all apps
    {cmd} delete NAME                     # Delete an app
""".format(cmd=CLI_COMMAND)
