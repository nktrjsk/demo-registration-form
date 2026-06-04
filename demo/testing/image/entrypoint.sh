#!/bin/sh
set -e

# Tests are run on-demand via `bitswan-coding-agent deployments exec`.
# Keep the container alive so it stays available between test runs.
exec tail -f /dev/null
