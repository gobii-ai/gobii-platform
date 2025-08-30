#!/bin/bash
set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_ROOT="$(cd "$PLATFORM_DIR/.." && pwd)"

# The source generator script
GENERATOR_SCRIPT="$PLATFORM_DIR/scripts/generate-ts-client.sh"

# Output directory for example client
OUTPUT_DIR="$SCRIPT_DIR/src/generated"

# Make sure the output directory exists
mkdir -p "$OUTPUT_DIR"

# Config file path
CONFIG_FILE="$PLATFORM_DIR/scripts/openapitools.json"

# Call the main generation script with explicit config file
"$GENERATOR_SCRIPT" "0.1.0" "$OUTPUT_DIR" "$CONFIG_FILE"

echo "Generated client for ts-example-client"
echo "Remember to run 'npm run build' to incorporate the changes"