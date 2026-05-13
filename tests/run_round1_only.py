"""Run ONLY Round 1 of the 4-round rolling test. Generate reports."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import from the test module
from test_4round_rolling import OUTPUT_DIR, ROUND_1, run_round

print("=" * 60)
print("  RUNNING ROUND 1 ONLY — 8 users, ~18 LLM calls")
print("=" * 60)

all_history = []

# Use the existing run_round function which handles everything
perfect1, results1, summary1 = run_round(1, ROUND_1, all_history)

# Print issue summary
print(f"\n{'=' * 60}")
print("  ROUND 1 ISSUE ANALYSIS")
print(f"{'=' * 60}")

all_issues = []
for r in results1:
    for rd in r["results"]:
        all_issues.extend(rd.get("issues", []))

issue_counts = {}
for iss in all_issues:
    issue_counts[iss] = issue_counts.get(iss, 0) + 1

print("\nTop issues:")
for iss, cnt in sorted(issue_counts.items(), key=lambda x: -x[1]):
    print(f"  - {iss}: {cnt}×")

print(f"\n{'=' * 60}")
print(f"  OVERALL: {summary1['overall_avg']:.1f}/5")
print(f"  ✅ OK: {summary1['ok']} | ⚠️ WARN: {summary1['warn']} | ❌ FAIL: {summary1['fail']}")
print(f"  Perfect users: {summary1['perfect_users']}")
print(f"{'=' * 60}")

print(f"\n📄 MD Report:  {OUTPUT_DIR / 'test_round1_report.md'}")
print(f"📄 JSON Report: {OUTPUT_DIR / 'test_round1_report.json'}")
print("\nDone.")
