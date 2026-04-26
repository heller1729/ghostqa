"""
GhostQA Evaluation Batch Runner

Runs the full evaluation matrix:
  - 3 methods: ghostqa-turbo, ghostqa-standard, random-baseline
  - 5 apps: juice-shop, academybugs, the-internet, saucedemo, toolshop
  - 3 models (turbo only): gemini-2.5-flash, gpt-4o, claude-sonnet-4-20250514
  - 1 run per combination (increase for statistical significance)

Usage:
    python run_evaluation.py                         # Full matrix
    python run_evaluation.py --apps juice-shop       # Single app
    python run_evaluation.py --methods turbo         # Single method
    python run_evaluation.py --skip-scans            # Only evaluate existing reports
"""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

# Configuration
APPS = {
    "juice-shop": {
        "url": "http://localhost:3000",
        "context": "Find all visual, functional, accessibility, and security bugs. Check product pages, login, registration, cart, and contact forms.",
        "needs_docker": True,
    },
    "academybugs": {
        "url": "https://academybugs.com/find-bugs/",
        "context": "Find all visual, functional, and layout bugs. Check product listings, shopping cart, sorting, and navigation.",
        "needs_docker": False,
    },
    "the-internet": {
        "url": "https://the-internet.herokuapp.com",
        "context": "Find all visual, functional, and accessibility bugs. Visit as many test pages as possible including broken images, typos, disappearing elements, and form authentication.",
        "needs_docker": False,
    },
    "saucedemo": {
        "url": "https://www.saucedemo.com",
        "context": "Find all visual, functional, and accessibility bugs. Login with problem_user/secret_sauce and check inventory, cart, and checkout. Look for broken images, wrong product info, and form issues.",
        "needs_docker": False,
    },
    "toolshop": {
        "url": "https://practicesoftwaretesting.com",
        "context": "Find all visual, functional, and accessibility bugs. Check product listings, filters, search, login, registration, and checkout flow.",
        "needs_docker": False,
    },
}

MODELS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
}

MAX_STEPS = 20
EVAL_DIR = Path("eval_reports")
GT_PATH = "benchmarks/ground_truth.json"


def run_scan(app_id: str, method: str, model_key: str = "gemini", run_id: int = 1) -> Optional[str]:
    """Run a single scan and return the report path."""
    app = APPS[app_id]
    
    # Create output directory: eval_reports/<app>/<method>_<model>_run<N>/
    exp_dir = EVAL_DIR / app_id / f"{method}_{model_key}_run{run_id}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    
    if method == "baseline":
        cmd = [
            sys.executable, "-m", "ghostqa", "baseline",
            app["url"],
            "--max-steps", str(MAX_STEPS),
            "--headless",
            "--output", str(exp_dir),
        ]
    else:
        provider = model_key
        if model_key == "gemini":
            provider = "google"
        
        cmd = [
            sys.executable, "-m", "ghostqa", "scan",
            app["url"],
            "--max-steps", str(MAX_STEPS),
            "--headless",
            "--provider", provider,
            "--model", MODELS[model_key],
            "--output", str(exp_dir),
            "--context", app["context"],
            "--fresh",
        ]
        
        if method == "turbo":
            cmd.append("--turbo")
    
    print(f"\n{'='*60}")
    print(f"  Running: {app_id} | {method} | {model_key} | run {run_id}")
    print(f"  Command: {' '.join(cmd)}")
    print(f"{'='*60}")
    
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            timeout=600,  # 10 min max per scan
            capture_output=True,
            text=True,
        )
        elapsed = time.time() - start
        print(f"  Completed in {elapsed:.0f}s (exit code: {result.returncode})")
        
        if result.returncode != 0:
            print(f"  STDERR: {result.stderr[:500]}")
        
        # Find the report file
        report_files = sorted(exp_dir.glob("*.json"))
        if report_files:
            report_path = str(report_files[-1])
            print(f"  Report: {report_path}")
            return report_path
        else:
            print("  WARNING: No report file generated")
            return None
            
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT after 600s")
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def evaluate_report_file(report_path: str) -> Optional[Dict[str, Any]]:
    """Evaluate a single report."""
    try:
        from evaluate import evaluate_report
        return evaluate_report(report_path, GT_PATH, threshold=0.25, verbose=False)
    except Exception as e:
        print(f"  Evaluation error: {e}")
        return None


def run_full_evaluation(
    apps: Optional[List[str]] = None,
    methods: Optional[List[str]] = None,
    models: Optional[List[str]] = None,
    runs: int = 1,
    skip_scans: bool = False,
):
    """Run the full evaluation matrix."""
    apps = apps or list(APPS.keys())
    methods = methods or ["turbo", "baseline"]
    models = models or ["gemini"]
    
    EVAL_DIR.mkdir(exist_ok=True)
    
    all_results = []
    scan_count = 0
    total_scans = 0
    
    # Count total scans
    for app_id in apps:
        for method in methods:
            if method in ("turbo", "standard"):
                for model_key in models:
                    total_scans += runs
            else:  # baseline
                total_scans += runs
    
    print(f"\n{'#'*60}")
    print(f"  GhostQA Evaluation Pipeline")
    print(f"  Apps: {apps}")
    print(f"  Methods: {methods}")
    print(f"  Models: {models}")
    print(f"  Runs per config: {runs}")
    print(f"  Total scans: {total_scans}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")
    
    for app_id in apps:
        for method in methods:
            model_list = models if method in ("turbo", "standard") else ["none"]
            
            for model_key in model_list:
                for run_id in range(1, runs + 1):
                    scan_count += 1
                    print(f"\n[{scan_count}/{total_scans}]", end="")
                    
                    if skip_scans:
                        # Try to find existing report
                        exp_dir = EVAL_DIR / app_id / f"{method}_{model_key}_run{run_id}"
                        report_files = sorted(exp_dir.glob("*.json")) if exp_dir.exists() else []
                        report_path = str(report_files[-1]) if report_files else None
                    else:
                        actual_model = model_key if method != "baseline" else "none"
                        report_path = run_scan(app_id, method, actual_model, run_id)
                    
                    if report_path:
                        result = evaluate_report_file(report_path)
                        if result and "error" not in result:
                            result["experiment"] = {
                                "app": app_id,
                                "method": method,
                                "model": model_key if method != "baseline" else "baseline-random",
                                "run_id": run_id,
                            }
                            all_results.append(result)
                            m = result["metrics"]
                            print(f"  => P={m['precision']:.2f} R={m['recall']:.2f} F1={m['f1']:.2f}")
    
    # Save all results
    results_path = EVAL_DIR / f"eval_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    
    # Print summary table
    print_summary(all_results)
    
    print(f"\n  Full results saved to: {results_path}")
    print(f"  Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    return all_results


def print_summary(results: List[Dict[str, Any]]):
    """Print a summary table of evaluation results."""
    if not results:
        print("\n  No results to summarize.")
        return
    
    print(f"\n{'='*80}")
    print(f"  EVALUATION SUMMARY")
    print(f"{'='*80}")
    print(f"  {'App':<16} {'Method':<10} {'Model':<22} {'Bugs':>5} {'TP':>4} {'FP':>4} {'FN':>4} {'P':>6} {'R':>6} {'F1':>6}")
    print(f"  {'-'*78}")
    
    for r in results:
        exp = r.get("experiment", {})
        m = r.get("metrics", {})
        print(f"  {exp.get('app', '?'):<16} "
              f"{exp.get('method', '?'):<10} "
              f"{exp.get('model', '?'):<22} "
              f"{r.get('reported_bugs', 0):>5} "
              f"{m.get('true_positives', 0):>4} "
              f"{m.get('false_positives', 0):>4} "
              f"{m.get('false_negatives', 0):>4} "
              f"{m.get('precision', 0):>5.0%} "
              f"{m.get('recall', 0):>5.0%} "
              f"{m.get('f1', 0):>5.0%}")
    
    # Aggregate by method
    from collections import defaultdict
    method_agg = defaultdict(lambda: {"p": [], "r": [], "f1": []})
    for r in results:
        exp = r.get("experiment", {})
        m = r.get("metrics", {})
        key = f"{exp.get('method', '?')}|{exp.get('model', '?')}"
        method_agg[key]["p"].append(m.get("precision", 0))
        method_agg[key]["r"].append(m.get("recall", 0))
        method_agg[key]["f1"].append(m.get("f1", 0))
    
    print(f"\n  {'AGGREGATE BY METHOD'}")
    print(f"  {'-'*50}")
    print(f"  {'Method + Model':<35} {'Avg P':>7} {'Avg R':>7} {'Avg F1':>7}")
    print(f"  {'-'*50}")
    for key, vals in sorted(method_agg.items()):
        n = len(vals["p"])
        avg_p = sum(vals["p"]) / n
        avg_r = sum(vals["r"]) / n
        avg_f1 = sum(vals["f1"]) / n
        print(f"  {key:<35} {avg_p:>6.0%} {avg_r:>6.0%} {avg_f1:>6.0%}")
    
    print(f"{'='*80}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="GhostQA Evaluation Batch Runner")
    parser.add_argument("--apps", nargs="+", default=None,
                       choices=list(APPS.keys()),
                       help="Apps to evaluate (default: all)")
    parser.add_argument("--methods", nargs="+", default=None,
                       choices=["turbo", "standard", "baseline"],
                       help="Methods to run (default: turbo, baseline)")
    parser.add_argument("--models", nargs="+", default=None,
                       choices=list(MODELS.keys()),
                       help="Models to test (default: gemini)")
    parser.add_argument("--runs", type=int, default=1,
                       help="Number of runs per configuration")
    parser.add_argument("--skip-scans", action="store_true",
                       help="Skip running scans, only evaluate existing reports")
    
    args = parser.parse_args()
    
    run_full_evaluation(
        apps=args.apps,
        methods=args.methods,
        models=args.models,
        runs=args.runs,
        skip_scans=args.skip_scans,
    )


if __name__ == "__main__":
    main()
