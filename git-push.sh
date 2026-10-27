#!/bin/bash
# Git push helper that bypasses Cursor/VSCode credential helper issues

cd /home/erp/frappe-bench/apps/foodics_migration

# Unset problematic environment variables
unset GIT_ASKPASS
unset SSH_ASKPASS
unset VSCODE_GIT_ASKPASS_NODE

# Force terminal prompts
export GIT_TERMINAL_PROMPT=1

# Run git push with the specified arguments
git push "$@"




















