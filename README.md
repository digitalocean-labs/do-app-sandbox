# DO App Console

> **Experimental**: This is a personal project and is not officially supported by DigitalOcean. APIs may change without notice.

A Python SDK for connecting to and troubleshooting **existing** DigitalOcean App Platform apps.

**Need to create temporary sandbox environments?** See [do-app-sandbox](https://github.com/bikramkgupta/do-app-sandbox) — the same toolkit focused on creating disposable containers.

## What This Package Does

**DO App Console** is designed for **connecting to existing App Platform apps** for troubleshooting and diagnostics:

```python
from do_app_sandbox import Sandbox

# Connect to your existing production app
app = Sandbox.get_from_id(
    app_id="ea1525eb-7e39-4fc5-91d4-5c8dc187581f",
    component="web"  # Your service/worker name
)

# Run diagnostics
app.exec("ps aux")                              # Check running processes
app.exec("df -h")                               # Check disk usage
app.exec("cat /var/log/app.log | tail -100")    # View recent logs
app.filesystem.download_file("/var/log/app.log", "./app.log")  # Download logs
```

Use cases:
- **Production debugging**: Inspect running processes, environment, logs
- **Configuration inspection**: Read config files, check environment variables
- **Log retrieval**: Download log files for analysis
- **Network diagnostics**: Check ports, connections, DNS resolution
- **File operations**: Upload hotfixes, download outputs

## Features

- **Connect to any app**: Works with any running App Platform app (not just ones you created)
- **Execute commands**: Run shell commands with exit code capture
- **File operations**: Read, write, upload, and download files (Spaces-backed for large files)
- **Process inspection**: List processes, check resource usage
- **Async support**: Both synchronous and asynchronous APIs
- **CLI tool**: Quick diagnostics from the command line

## Installation

```bash
pip install do-app-console
```

### Prerequisites

1. **Python 3.10.12+**: Required for secure tarfile extraction
2. **doctl CLI**: Must be installed and authenticated (`doctl auth init`)

## Quick Start

### Find Your App ID and Component Name

```bash
# List your apps
doctl apps list

# Get component names for an app
doctl apps get <APP_ID> --output json | jq '.spec.services[].name'
doctl apps get <APP_ID> --output json | jq '.spec.workers[].name'
```

### Connect and Troubleshoot

```python
from do_app_sandbox import Sandbox

# Connect to your app
app = Sandbox.get_from_id(
    app_id="your-app-id",
    component="your-component-name"
)

# System inspection
print(app.exec("whoami").stdout)           # Current user
print(app.exec("pwd").stdout)              # Working directory
print(app.exec("uname -a").stdout)         # System info

# Process inspection
print(app.exec("ps aux").stdout)           # All processes
print(app.exec("top -bn1 | head -20").stdout)  # Resource usage

# Log inspection
print(app.exec("tail -100 /var/log/app.log").stdout)

# Environment inspection
print(app.exec("env | sort").stdout)       # All env vars

# Network inspection
print(app.exec("netstat -tlnp").stdout)    # Listening ports
```

### CLI Usage

```bash
# Execute a command on your app
console exec --id ea1525eb-7e39-4fc5-91d4-5c8dc187581f "ps aux"

# Check disk usage
console exec --id YOUR_APP_ID "df -h"

# View environment
console exec --id YOUR_APP_ID "env | sort"
```

## Common Diagnostic Commands

| Task | Command |
|------|---------|
| Check running processes | `app.exec("ps aux")` |
| View recent logs | `app.exec("tail -100 /var/log/app.log")` |
| Check disk usage | `app.exec("df -h")` |
| Inspect environment | `app.exec("env \| sort")` |
| Check listening ports | `app.exec("netstat -tlnp")` |
| View memory usage | `app.exec("free -m")` |
| Check file permissions | `app.exec("ls -la /app")` |
| Read config file | `app.filesystem.read_file("/app/config.json")` |
| Download logs | `app.filesystem.download_file("/var/log/app.log", "./app.log")` |

## File Operations

```python
# Read files from the app
config = app.filesystem.read_file("/app/config.json")
print(config)

# List directory contents
files = app.filesystem.list_dir("/app")
for f in files:
    print(f"{f.name} ({f.type})")

# Download files for local analysis
app.filesystem.download_file("/var/log/app.log", "./app.log")
app.filesystem.download_file("/app/output.csv", "./output.csv")

# Upload a hotfix (use with caution!)
app.filesystem.upload_file("./hotfix.py", "/app/hotfix.py")
```

## Large File Transfers

For files larger than 250KB, configure Spaces for efficient transfers:

```bash
export SPACES_ACCESS_KEY=your-key
export SPACES_SECRET_KEY=your-secret
export SPACES_BUCKET=your-bucket
export SPACES_REGION=nyc3
```

```python
# Download large log files
app.filesystem.download_large("/var/log/large-app.log", "./large-app.log")
```

## API Reference

### Sandbox.get_from_id()

```python
app = Sandbox.get_from_id(
    app_id="your-app-id",      # Required: App Platform app ID
    component="web",            # Required: Service or worker name
    api_token=None,            # Optional: Uses doctl auth if not provided
    spaces_config=None         # Optional: For large file transfers
)
```

### Available Methods

- `exec(command, env, cwd, timeout)` - Execute a command
- `filesystem.read_file(path)` - Read a file
- `filesystem.write_file(path, content)` - Write a file
- `filesystem.upload_file(local, remote)` - Upload a file
- `filesystem.download_file(remote, local)` - Download a file
- `filesystem.list_dir(path)` - List directory contents
- `get_url()` - Get the app's public URL

## Related Projects

This is part of 3 projects to scale Agentic workflows with DigitalOcean App Platform:
- [do-app-devcontainer](https://github.com/bikramkgupta/do-app-devcontainer) — Safe local sandboxing using DevContainers
- [do-app-hot-reload-template](https://github.com/bikramkgupta/do-app-hot-reload-template) — Rapid development iteration using hot reload
- [do-app-sandbox](https://github.com/bikramkgupta/do-app-sandbox) — Create disposable sandbox environments

## Important Notes

1. **Read-only recommended**: While you can write files, be careful not to disrupt running applications.
2. **No credentials stored**: Uses your existing doctl authentication.
3. **Shell compatibility**: Container shell must have a standard prompt ending with `$ ` or `# `.

## License

MIT
