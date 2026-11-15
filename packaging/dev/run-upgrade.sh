#!/usr/bin/env bash
# Works through upgrade-plan.md's task list with DeepSeek (Command Code), one task per run, in the deepseek-rest worktree.
# Usage: packaging/dev/run-upgrade.sh [first-task-id] or [T1 T2 ...]   Log: <worktree>/upgrade-progress.md and <worktree>/.upgrade-logs/
set -u
WT=/data/Sideprojects/shoggoth-wt/deepseek-rest
CC="node $(npm root -g)/command-code/dist/index.mjs"
TASKS="S1 S2 S3 S4 S5 S6 S7 S8 S9 S10 S11 S12 U1 U2 U3 U4 U5 U6 U7 U8 S13 W1 W2 W3 W4"
START="${1:-S1}"
[ $# -gt 1 ] && TASKS="$*"   # several ids: run just those, in that order
cd "$WT" || exit 1
mkdir -p .upgrade-logs
grep -qx ".upgrade-logs/" .git/info/exclude 2>/dev/null || echo ".upgrade-logs/" >> "$(git rev-parse --git-common-dir)/info/exclude"
[ -f upgrade-progress.md ] || printf "# Upgrade progress\n\nOne entry per task from upgrade-plan.md, written by packaging/dev/run-upgrade.sh.\n" > upgrade-progress.md
started=0
for T in $TASKS; do
  [ "$T" = "$START" ] && started=1
  [ $started = 1 ] || continue
  before=$(git rev-parse HEAD)
  log=".upgrade-logs/$T.log"
  brief="You are working in the git worktree $WT on branch deepseek-rest. Read upgrade-plan.md fully (rules first), then do task $T only, \
following its text and the master-plan section it names. Obey every rule in 'Rules for every task' (never git stash, never edit outside this worktree, \
never load anything on the RTX 4080, stdlib only, tests must pass). Commit your work with messages starting '$T:'. \
Finish with a short plain report: what you did, files changed, tests run and their result, and anything not done or not verified."
  echo "=== $T started $(date -Is)" > "$log"
  timeout 5400 $CC -p "$brief" -m deepseek/deepseek-v4.1-flash -t --yolo --max-turns 300 --skip-onboarding --no-auto-update >> "$log" 2>&1
  rc=$?
  # One repair pass when the suite fails after the task.
  tests=$(python3 -W ignore -m unittest discover tests 2>&1 | tail -4 | tr '\n' ' ')
  if ! echo "$tests" | grep -q "^.*OK"; then
    echo "=== $T repair pass $(date -Is): $tests" >> "$log"
    timeout 2700 $CC -p "In $WT (branch deepseek-rest), after task $T of upgrade-plan.md the test suite fails: $tests. Run python3 -W ignore -m unittest discover tests, \
fix the cause (never by deleting or skipping a test, never git stash), and commit with a message starting '$T fix:'. Report briefly." \
      -m deepseek/deepseek-v4.1-flash -t --yolo --max-turns 80 --skip-onboarding --no-auto-update >> "$log" 2>&1
    tests=$(python3 -W ignore -m unittest discover tests 2>&1 | tail -4 | tr '\n' ' ')
  fi
  commits=$(git log --oneline "$before"..HEAD | wc -l)
  report=$(tail -c 2500 "$log" | sed 's/\x1b\[[0-9;]*m//g')
  {
    printf "\n## %s (%s)\n\n- exit code %s, %s new commit(s), tests: %s\n\n<details><summary>Worker report (tail)</summary>\n\n\`\`\`\n%s\n\`\`\`\n</details>\n" \
      "$T" "$(date -Is)" "$rc" "$commits" "$tests" "$report"
  } >> upgrade-progress.md
  git add upgrade-progress.md && git commit -qm "Progress log: $T" -m "Co-Authored-By: DeepSeek V4.1 Flash <noreply@commandcode.ai>" || true
done
echo "=== all done $(date -Is)" >> .upgrade-logs/driver.log
