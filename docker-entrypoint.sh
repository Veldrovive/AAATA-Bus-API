#!/bin/sh
set -e

# If no arguments provided, default to running scripts/collect_data.py
if [ $# -eq 0 ]; then
    exec uv run python -u scripts/collect_data.py
fi

TARGET="$1"

# Case 1: Argument is a filename in scripts/ (e.g. "collect_data.py")
if [ -f "scripts/${TARGET}" ]; then
    shift
    exec uv run python -u "scripts/${TARGET}" "$@"

# Case 2: Argument is path like "scripts/collect_data.py"
elif [ -f "${TARGET}" ] && [ "$(dirname "${TARGET}")" = "scripts" ]; then
    shift
    exec uv run python -u "${TARGET}" "$@"

# Case 3: Argument is a script without extension (e.g. "collect_data")
elif [ -f "scripts/${TARGET}.py" ]; then
    shift
    exec uv run python -u "scripts/${TARGET}.py" "$@"

# Case 4: Helper commands that should run inside uv environment
elif [ "$TARGET" = "pytest" ] || [ "$TARGET" = "python" ]; then
    exec uv run "$@"

# Case 5: Any other system command (e.g. uv, bash, sh)
else
    exec "$@"
fi
