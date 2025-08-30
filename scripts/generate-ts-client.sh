#!/bin/bash
set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLATFORM_DIR="$PROJECT_ROOT/gobii_platform"  # This is the Django project directory
SCHEMA_PATH="$PROJECT_ROOT/schema.yaml"
TEMPLATES_DIR="$PROJECT_ROOT/typescript-fetch-templates"  # Path to custom templates

# Parse command line arguments
VERSION=${1:-"0.1.0"} # Use provided version or default to 0.1.0
OUTPUT_DIR=${2:-"$PROJECT_ROOT/ts-client"} # Use provided output dir or default to project root ts-client

echo "Generating TypeScript client v$VERSION"
echo "Output directory: $OUTPUT_DIR"
echo "Using templates from: $TEMPLATES_DIR"

# Ensure proper template structure (README.mustache and package.mustache at root level)
mkdir -p "$TEMPLATES_DIR"
if [ ! -f "$TEMPLATES_DIR/README.mustache" ] || [ ! -f "$TEMPLATES_DIR/package.mustache" ]; then
  echo "Copying templates to the correct directory structure..."
  cp -f "$PROJECT_ROOT/templates/typescript-fetch/README.mustache" "$TEMPLATES_DIR/"
  cp -f "$PROJECT_ROOT/templates/typescript-fetch/package.mustache" "$TEMPLATES_DIR/"
fi

# Change to the platform directory to run the schema generation command
cd "$PLATFORM_DIR"

# Generate the OpenAPI schema
echo "Generating OpenAPI schema..."
python manage.py spectacular --file "$SCHEMA_PATH" --validate

# Ensure the output directory exists (handle both absolute and relative paths)
if [[ "$OUTPUT_DIR" = /* ]]; then
    # Absolute path
    mkdir -p "$OUTPUT_DIR"
else
    # Relative path (relative to current directory after cd to platform dir)
    mkdir -p "$PLATFORM_DIR/../$OUTPUT_DIR"
fi

# Generate the TypeScript client
echo "Generating TypeScript client..."

# Prepare output directory path (handling both absolute and relative paths)
if [[ "$OUTPUT_DIR" = /* ]]; then
    # Absolute path
    FINAL_OUTPUT_DIR="$OUTPUT_DIR"
else
    # Relative path (from current location after cd to platform dir)
    FINAL_OUTPUT_DIR="$PLATFORM_DIR/../$OUTPUT_DIR"
fi

echo "Final output directory: $FINAL_OUTPUT_DIR"

npx @openapitools/openapi-generator-cli generate \
  -i "$SCHEMA_PATH" \
  -g typescript-fetch \
  -o "$FINAL_OUTPUT_DIR" \
  -t "$TEMPLATES_DIR" \
  --additional-properties=npmName=@gobii-ai/client,\
npmVersion=$VERSION,\
useSingleRequestParameter=false,\
typescriptThreePlus=true,\
supportsES6=true,\
stringEnums=true,\
modelPropertyNaming=camelCase

echo "Client generation complete!"
echo "Generated client in: $FINAL_OUTPUT_DIR"