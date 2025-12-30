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

PRODUCT_NAME = "sandbox"
PRODUCT_NAME_PLURAL = "sandboxes"
CLI_COMMAND = "sandbox"

# Package description (shown in --version and help)
PRODUCT_DESCRIPTION = "Manage sandbox environments on DigitalOcean App Platform"
PRODUCT_VERSION = "0.1.2"

# =============================================================================
# CLI HELP TEXT
# =============================================================================

HELP_MAIN = "Manage sandbox environments on DigitalOcean App Platform"
HELP_SETUP = "Build and push sandbox images to your DOCR registry"
HELP_CREATE = "Create a new sandbox"
HELP_LIST = "List all sandboxes"
HELP_DELETE = "Delete a sandbox"
HELP_EXEC = "Execute a command in a sandbox"
HELP_IMAGE = "Manage custom sandbox images"

# Argument help
HELP_ARG_NAME = "Name for the sandbox (auto-generated if not provided)"
HELP_ARG_TARGET = "Sandbox name or ID"
HELP_ARG_DELETE_NAME = "Sandbox name to delete"

# =============================================================================
# USER-FACING MESSAGES
# =============================================================================

# Create messages
MSG_CREATING = "Creating sandbox..."
MSG_CREATED = "Sandbox created successfully!"
MSG_CREATE_ERROR = "Error creating sandbox: {error}"

# List messages
MSG_LIST_EMPTY = "No sandboxes found."
MSG_LIST_ERROR = "Error listing sandboxes: {error}"

# Delete messages
MSG_DELETE_CONFIRM_ALL = "Are you sure you want to delete ALL sandboxes? [y/N]: "
MSG_DELETE_NONE = "No sandboxes to delete."
MSG_DELETED = "Deleted: {name} ({app_id})"
MSG_DELETE_FAILED = "Failed to delete {name}: {error}"
MSG_DELETE_COUNT = "Deleted {count} sandbox(es)."
MSG_DELETE_SUCCESS = "Deleted sandbox: {target}"
MSG_DELETE_ERROR = "Error deleting sandbox: {error}"
MSG_NOT_FOUND = "Sandbox '{name}' not found."

# Exec messages
MSG_EXEC_ERROR = "Error executing command: {error}"

# Status messages (for --no-wait)
MSG_STILL_DEPLOYING = "Note: Sandbox may still be deploying. Use '{cmd} list' to check status."

# =============================================================================
# DOCSTRINGS (for module-level documentation)
# =============================================================================

CLI_MODULE_DOC = """Command-line interface for App Platform Sandbox.

This module provides a CLI for managing sandbox environments on DigitalOcean App Platform.

Usage:
    {cmd} setup --registry MY_REGISTRY    # Build and push images
    {cmd} create --image python           # Create a new sandbox
    {cmd} list                            # List all sandboxes
    {cmd} delete NAME                     # Delete a sandbox
    {cmd} exec NAME "command"             # Execute a command
""".format(cmd=CLI_COMMAND)
