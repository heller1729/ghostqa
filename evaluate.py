"""
GhostQA Evaluation Script

Compares agent scan reports against ground truth benchmarks
to compute Precision, Recall, and F1 scores.

Usage:
    python evaluate.py <report.json> [--ground-truth benchmarks/ground_truth.json] [--threshold 0.25]
    python evaluate.py reports/ --all  # Evaluate all reports in a directory
"""

import json
import sys
import re
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
from collections import defaultdict


def load_ground_truth(gt_path: str = "benchmarks/ground_truth.json") -> Dict[str, Any]:
    """Load ground truth benchmark file."""
    with open(gt_path) as f:
        return json.load(f)


def load_report(report_path: str) -> Dict[str, Any]:
    """Load a GhostQA scan report."""
    with open(report_path) as f:
        return json.load(f)


def detect_app(report: Dict[str, Any], ground_truth: Dict[str, Any]) -> Optional[str]:
    """Detect which app a report is for based on URL."""
    url = report.get("url", "").lower()
    
    # Direct pattern matching
    if "localhost:3000" in url or "juice" in url or "owasp" in url:
        return "juice-shop"
    if "academybugs" in url:
        return "academybugs"
    if "the-internet" in url or "herokuapp" in url:
        return "the-internet"
    if "saucedemo" in url or "swag" in url:
        return "saucedemo"
    if "practicesoftwaretesting" in url or "toolshop" in url:
        return "toolshop"
    
    return None


def tokenize(text: str) -> set:
    """Tokenize text into lowercase word set."""
    return set(re.findall(r'[a-z0-9]+', text.lower()))


def compute_similarity(reported_desc: str, gt_bug: Dict[str, Any]) -> float:
    """
    Compute similarity between a reported bug description and a ground truth bug.
    
    Uses keyword matching + word overlap for robust fuzzy matching.
    """
    reported_lower = reported_desc.lower()
    reported_tokens = tokenize(reported_desc)
    
    if not reported_tokens:
        return 0.0
    
    # 1. Direct keyword hits (weighted heavily)
    keywords = gt_bug.get("keywords", [])
    keyword_hits = sum(1 for kw in keywords if kw.lower() in reported_lower)
    keyword_score = keyword_hits / max(len(keywords), 1)
    
    # 2. Word overlap with ground truth description
    gt_tokens = tokenize(gt_bug.get("description", ""))
    if gt_tokens:
        overlap = len(reported_tokens & gt_tokens)
        overlap_score = overlap / max(min(len(reported_tokens), len(gt_tokens)), 1)
    else:
        overlap_score = 0.0
    
    # 3. Type match bonus
    reported_type = ""
    for t in ["accessibility", "layout", "visual", "security", "functional", "text", "price", "error"]:
        if t in reported_lower:
            reported_type = t
            break
    type_bonus = 0.1 if reported_type == gt_bug.get("type", "") else 0.0
    
    # Combined score (keyword-heavy)
    score = (keyword_score * 0.5) + (overlap_score * 0.4) + type_bonus
    
    return min(score, 1.0)


def match_bugs(
    reported_bugs: List[Dict[str, Any]],
    gt_bugs: List[Dict[str, Any]],
    threshold: float = 0.25,
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Match reported bugs to ground truth using greedy best-match assignment.
    
    Returns:
        (true_positives, false_positives, false_negatives)
        
    Each TP entry: {"reported": {...}, "ground_truth": {...}, "similarity": float}
    """
    # Compute all pairwise similarities
    pairs = []
    for r_idx, reported in enumerate(reported_bugs):
        for g_idx, gt in enumerate(gt_bugs):
            sim = compute_similarity(reported.get("description", ""), gt)
            if sim >= threshold:
                pairs.append((sim, r_idx, g_idx))
    
    # Sort by similarity descending (greedy best-first matching)
    pairs.sort(key=lambda x: x[0], reverse=True)
    
    matched_reported = set()
    matched_gt = set()
    true_positives = []
    
    for sim, r_idx, g_idx in pairs:
        if r_idx in matched_reported or g_idx in matched_gt:
            continue
        
        matched_reported.add(r_idx)
        matched_gt.add(g_idx)
        true_positives.append({
            "reported": reported_bugs[r_idx],
            "ground_truth": gt_bugs[g_idx],
            "similarity": round(sim, 3),
        })
    
    # False positives: reported but not matched to any ground truth
    false_positives = [
        {"reported": reported_bugs[i]}
        for i in range(len(reported_bugs))
        if i not in matched_reported
    ]
    
    # False negatives: ground truth bugs not found by agent
    false_negatives = [
        {"ground_truth": gt_bugs[i]}
        for i in range(len(gt_bugs))
        if i not in matched_gt
    ]
    
    return true_positives, false_positives, false_negatives


def compute_metrics(tp: int, fp: int, fn: int) -> Dict[str, float]:
    """Compute precision, recall, F1."""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }


def evaluate_report(
    report_path: str,
    gt_path: str = "benchmarks/ground_truth.json",
    threshold: float = 0.25,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Evaluate a single scan report against ground truth.
    
    Returns evaluation results dict.
    """
    gt_data = load_ground_truth(gt_path)
    report = load_report(report_path)
    
    # Detect which app this report is for
    app_id = detect_app(report, gt_data)
    if not app_id:
        print(f"Could not detect app for report URL: {report.get('url')}")
        print(f"Known apps: {list(gt_data.get('apps', {}).keys())}")
        return {"error": "unknown_app"}
    
    app_info = gt_data["apps"][app_id]
    
    # Filter ground truth bugs for this app
    gt_bugs = [b for b in gt_data["bugs"] if b["app"] == app_id]
    reported_bugs = report.get("bugs", [])
    
    if verbose:
        print(f"\n{'='*60}")
        print(f"  GhostQA Evaluation Report")
        print(f"{'='*60}")
        print(f"  App:       {app_info['name']} ({app_id})")
        print(f"  Report:    {Path(report_path).name}")
        print(f"  Model:     {report.get('model', '?')}")
        print(f"  Mode:      {'Turbo' if report.get('turbo_mode') else 'Standard'}")
        print(f"  Steps:     {report.get('steps_taken', '?')}")
        print(f"  Duration:  {report.get('duration_seconds', 0):.0f}s")
        print(f"  Pages:     {len(report.get('pages_visited', []))}")
        print(f"  Reported:  {len(reported_bugs)} bugs")
        print(f"  GT Total:  {len(gt_bugs)} bugs")
        print(f"  Threshold: {threshold}")
        print(f"{'='*60}")
    
    # Match bugs
    tp_list, fp_list, fn_list = match_bugs(reported_bugs, gt_bugs, threshold)
    
    # Overall metrics
    metrics = compute_metrics(len(tp_list), len(fp_list), len(fn_list))
    
    if verbose:
        print(f"\n  RESULTS:")
        print(f"  Precision: {metrics['precision']:.2%}")
        print(f"  Recall:    {metrics['recall']:.2%}")
        print(f"  F1 Score:  {metrics['f1']:.2%}")
        print(f"  TP: {metrics['true_positives']}  FP: {metrics['false_positives']}  FN: {metrics['false_negatives']}")
    
    # Per-type breakdown
    type_stats = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    
    for tp in tp_list:
        bug_type = tp["ground_truth"].get("type", "unknown")
        type_stats[bug_type]["tp"] += 1
    
    for fp in fp_list:
        bug_type = fp["reported"].get("type", "unknown")
        type_stats[bug_type]["fp"] += 1
    
    for fn in fn_list:
        bug_type = fn["ground_truth"].get("type", "unknown")
        type_stats[bug_type]["fn"] += 1
    
    if verbose and type_stats:
        print(f"\n  PER-TYPE BREAKDOWN:")
        print(f"  {'Type':<15} {'TP':>4} {'FP':>4} {'FN':>4} {'P':>7} {'R':>7} {'F1':>7}")
        print(f"  {'-'*52}")
        for bug_type in sorted(type_stats.keys()):
            s = type_stats[bug_type]
            m = compute_metrics(s["tp"], s["fp"], s["fn"])
            print(f"  {bug_type:<15} {s['tp']:>4} {s['fp']:>4} {s['fn']:>4} {m['precision']:>6.0%} {m['recall']:>6.0%} {m['f1']:>6.0%}")
    
    # Matched bugs detail
    if verbose and tp_list:
        print(f"\n  MATCHED BUGS (True Positives):")
        for i, tp in enumerate(tp_list, 1):
            gt_id = tp["ground_truth"]["id"]
            sim = tp["similarity"]
            desc = tp["reported"]["description"][:70]
            print(f"  {i:>3}. [{gt_id}] (sim={sim:.2f}) {desc}...")
    
    # Missed bugs detail
    if verbose and fn_list:
        print(f"\n  MISSED BUGS (False Negatives):")
        for i, fn_entry in enumerate(fn_list, 1):
            gt = fn_entry["ground_truth"]
            print(f"  {i:>3}. [{gt['id']}] {gt['description'][:70]}...")
    
    if verbose:
        print(f"\n{'='*60}\n")
    
    # Build result
    result = {
        "app": app_id,
        "report_file": str(report_path),
        "model": report.get("model", "unknown"),
        "turbo_mode": report.get("turbo_mode", False),
        "steps": report.get("steps_taken", 0),
        "duration_seconds": report.get("duration_seconds", 0),
        "pages_visited": len(report.get("pages_visited", [])),
        "reported_bugs": len(reported_bugs),
        "ground_truth_bugs": len(gt_bugs),
        "metrics": metrics,
        "per_type": {
            k: compute_metrics(v["tp"], v["fp"], v["fn"])
            for k, v in type_stats.items()
        },
        "true_positives": tp_list,
        "false_positives": fp_list,
        "false_negatives": fn_list,
    }
    
    return result


def evaluate_directory(
    reports_dir: str,
    gt_path: str = "benchmarks/ground_truth.json",
    threshold: float = 0.25,
) -> List[Dict[str, Any]]:
    """Evaluate all JSON reports in a directory."""
    reports_path = Path(reports_dir)
    report_files = sorted(reports_path.glob("report_*.json"))
    
    if not report_files:
        print(f"No report files found in {reports_dir}")
        return []
    
    results = []
    for rf in report_files:
        try:
            result = evaluate_report(str(rf), gt_path, threshold, verbose=False)
            if "error" not in result:
                results.append(result)
                m = result["metrics"]
                mode = "Turbo" if result["turbo_mode"] else "Std"
                print(f"  {rf.name}: P={m['precision']:.2f} R={m['recall']:.2f} F1={m['f1']:.2f} ({mode}, {result['model']})")
        except Exception as e:
            print(f"  {rf.name}: ERROR - {e}")
    
    if results:
        # Aggregate summary
        avg_p = sum(r["metrics"]["precision"] for r in results) / len(results)
        avg_r = sum(r["metrics"]["recall"] for r in results) / len(results)
        avg_f1 = sum(r["metrics"]["f1"] for r in results) / len(results)
        
        print(f"\n  AGGREGATE ({len(results)} reports):")
        print(f"  Avg Precision: {avg_p:.2%}")
        print(f"  Avg Recall:    {avg_r:.2%}")
        print(f"  Avg F1:        {avg_f1:.2%}")
    
    return results


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="GhostQA Evaluation Script")
    parser.add_argument("report", help="Path to report JSON file or directory of reports")
    parser.add_argument("--ground-truth", "-g", default="benchmarks/ground_truth.json",
                       help="Path to ground truth benchmark file")
    parser.add_argument("--threshold", "-t", type=float, default=0.25,
                       help="Similarity threshold for matching (default: 0.25)")
    parser.add_argument("--all", action="store_true",
                       help="Evaluate all reports in the directory")
    parser.add_argument("--output", "-o", help="Save results to JSON file")
    
    args = parser.parse_args()
    
    path = Path(args.report)
    
    if path.is_dir() or args.all:
        results = evaluate_directory(str(path), args.ground_truth, args.threshold)
    elif path.is_file():
        results = [evaluate_report(str(path), args.ground_truth, args.threshold)]
    else:
        print(f"File not found: {args.report}")
        sys.exit(1)
    
    if args.output and results:
        output_path = Path(args.output)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results saved to {output_path}")


if __name__ == "__main__":
    main()
