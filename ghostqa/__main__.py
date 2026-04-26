"""
GhostQA CLI Entry Point

Usage:
    python -m ghostqa scan <url> [options]
"""

import typer
from rich.console import Console
from rich.panel import Panel
from typing import Optional
import asyncio

from ghostqa.agent import GhostQAAgent
from ghostqa.config import GhostQAConfig
from ghostqa.llm.factory import DEFAULT_MODELS

app = typer.Typer(
    name="ghostqa",
    help="🔍 GhostQA - Autonomous AI Web Application Testing Agent",
    add_completion=False,
)
console = Console()


@app.command()
def scan(
    url: str = typer.Argument(..., help="URL of the web application to test"),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Focus instructions for the agent, e.g. 'Navigate to /admin and test for SQL injection' or 'Focus on UI bugs on the checkout page'"
    ),
    username: Optional[str] = typer.Option(
        None, "--username", "-u", help="Username for authentication"
    ),
    password: Optional[str] = typer.Option(
        None, "--password", "-p", help="Password for authentication"
    ),
    max_steps: int = typer.Option(
        50, "--max-steps", "-s", help="Maximum exploration steps"
    ),
    headless: bool = typer.Option(
        True, "--headless/--no-headless", help="Run browser in headless mode"
    ),
    provider: str = typer.Option(
        "google", "--provider", help="LLM provider: google, openai, anthropic"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="Model name override"
    ),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="Enable debug logging"
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output directory for reports"
    ),
    fresh: bool = typer.Option(
        False, "--fresh", help="Ignore saved context, start a fresh scan"
    ),
    turbo: bool = typer.Option(
        False, "--turbo", help="Visual agent mode — 1 unified LLM call per step, 2-3× faster"
    ),
    record: bool = typer.Option(
        False, "--record", help="Record browser session as video"
    ),
):
    """
    🔍 Scan a web application for bugs and issues.
    
    Example:
        ghostqa scan https://example.com --context "E-commerce site"
    """
    # Resolve actual model name
    resolved_model = model or DEFAULT_MODELS.get(provider.lower(), "unknown")

    context_line = f"\nFocus: [yellow]{context}[/yellow]" if context else ""
    turbo_line = f"\nMode: [bold magenta]⚡ TURBO (visual agent)[/bold magenta]" if turbo else ""
    record_line = f"\nRecording: [bold red]🎥 Video ON[/bold red]" if record else ""
    console.print(Panel.fit(
        f"[bold blue]GhostQA[/bold blue] - Autonomous Web Testing Agent\n"
        f"Target: [green]{url}[/green]\n"
        f"Provider: [cyan]{provider}[/cyan]\n"
        f"Model: [cyan]{resolved_model}[/cyan]"
        f"{context_line}{turbo_line}{record_line}",
        title="🔍 Starting Scan",
    ))
    
    # Build configuration
    config = GhostQAConfig(
        url=url,
        context=context,
        username=username,
        password=password,
        max_steps=max_steps,
        headless=headless,
        llm_provider=provider,
        model=model,
        debug=debug,
        output_dir=output,
        fresh_context=fresh,
        turbo=turbo,
        record_video=record,
    )
    
    # Run the agent
    agent = GhostQAAgent(config)
    
    try:
        report = asyncio.run(agent.run())
        
        console.print(Panel.fit(
            f"[bold green]Scan Complete![/bold green]\n"
            f"Pages visited: {report.pages_visited}\n"
            f"Bugs found: {report.bugs_found}\n"
            f"Report saved to: {report.report_path}",
            title="✅ Results",
        ))
    except KeyboardInterrupt:
        console.print("\n[yellow]Scan interrupted by user[/yellow]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        if debug:
            console.print_exception()
        raise typer.Exit(1)


@app.command()
def version():
    """Show version information."""
    from ghostqa import __version__
    console.print(f"GhostQA version: [bold]{__version__}[/bold]")


@app.command(name="clear-context")
def clear_context(
    url: Optional[str] = typer.Argument(None, help="URL to clear context for (clears all if omitted)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output directory containing context"),
):
    """🗑️ Clear saved scan context files.
    
    Examples:
        ghostqa clear-context                           # Clear ALL context
        ghostqa clear-context http://localhost:3000      # Clear context for a specific URL
    """
    from pathlib import Path
    from ghostqa.context import ScanContext
    
    context_dir = Path(output or "reports") / "context"
    if not context_dir.exists():
        console.print("[yellow]No context directory found.[/yellow]")
        return
    
    if url:
        prefix = ScanContext.url_to_context_name(url)
        files = list(context_dir.glob(f"{prefix}*.md"))
    else:
        files = list(context_dir.glob("*.md"))
    
    if not files:
        console.print(f"[yellow]No context files found{f' for {url}' if url else ''}.[/yellow]")
        return
    
    for f in files:
        f.unlink()
        console.print(f"[red]🗑️  Deleted: {f.name}[/red]")
    
    console.print(f"\n[green]✅ Cleared {len(files)} context file(s).[/green]")


@app.command()
def baseline(
    url: str = typer.Argument(..., help="URL of the web application to test"),
    max_steps: int = typer.Option(20, "--max-steps", "-s", help="Maximum exploration steps"),
    headless: bool = typer.Option(True, "--headless/--no-headless", help="Run browser in headless mode"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output directory for reports"),
):
    """🎲 Run random explorer baseline (no LLM, DOM-only bug detection).
    
    Example:
        ghostqa baseline http://localhost:3000 --max-steps 20
    """
    from ghostqa.baseline import RandomExplorerBaseline
    
    console.print(Panel.fit(
        f"[bold blue]GhostQA Baseline[/bold blue] - Random Explorer\n"
        f"Target: [green]{url}[/green]\n"
        f"Mode: [cyan]Random clicks + DOM heuristics (no LLM)[/cyan]\n"
        f"Steps: [cyan]{max_steps}[/cyan]",
        title="🎲 Starting Baseline",
    ))
    
    config = GhostQAConfig(
        url=url,
        max_steps=max_steps,
        headless=headless,
        output_dir=output,
    )
    
    agent = RandomExplorerBaseline(config)
    
    try:
        report = asyncio.run(agent.run())
        console.print(Panel.fit(
            f"[bold green]Baseline Complete![/bold green]\n"
            f"Pages visited: {len(report.get('pages_visited', []))}\n"
            f"Bugs found: {report.get('summary', {}).get('total_bugs', 0)}\n"
            f"Report saved to: {report.get('report_path', '?')}",
            title="✅ Baseline Results",
        ))
    except KeyboardInterrupt:
        console.print("\n[yellow]Baseline interrupted by user[/yellow]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        console.print_exception()
        raise typer.Exit(1)


@app.command()
def evaluate(
    report: str = typer.Argument(..., help="Path to report JSON file or directory"),
    ground_truth: str = typer.Option("benchmarks/ground_truth.json", "--ground-truth", "-g", help="Ground truth file"),
    threshold: float = typer.Option(0.25, "--threshold", "-t", help="Matching similarity threshold"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Save results to JSON"),
):
    """📊 Evaluate a scan report against ground truth benchmark.
    
    Examples:
        ghostqa evaluate reports/report_20260422_002638.json
        ghostqa evaluate reports/ --ground-truth benchmarks/ground_truth.json
    """
    from pathlib import Path
    import sys
    sys.path.insert(0, ".")
    from evaluate import evaluate_report, evaluate_directory
    
    path = Path(report)
    if path.is_dir():
        results = evaluate_directory(str(path), ground_truth, threshold)
    elif path.is_file():
        results = [evaluate_report(str(path), ground_truth, threshold)]
    else:
        console.print(f"[red]File not found: {report}[/red]")
        raise typer.Exit(1)
    
    if output and results:
        import json
        with open(output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        console.print(f"\n[green]Results saved to {output}[/green]")


if __name__ == "__main__":
    app()
