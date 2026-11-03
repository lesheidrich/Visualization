"""Console entry point; calculations live in feature modules."""
import argparse
import sys

COMMANDS = {
    "thrpt": "Plot throughput for every rerun and the mean of reruns.",
    "speedup": "Plot same-rerun baseline speedup and the ratio of means across reruns.",
    "gc": "Tabulate GC, pause and batch-fill metrics per rerun and as mean of reruns; plot pauses_max per rerun, gc_alloc_rate_norm as mean of reruns and batch_fill as mean of reruns for mb2_rawFinalize.",
    "compare": "Tabulate the paired 2A vs 2B comparison with an equivalence verdict per thread count; also plot the ratio per thread count.",
    "help": "List project commands and their options.",
}


def _parser(command):
    parser = argparse.ArgumentParser(prog=command, description=COMMANDS[command])
    if command != "help":
        parser.add_argument("--config", metavar="PATH", default="config.yaml",
                            help="YAML configuration path (default: %(default)s)")
    return parser


def help_main(argv=None):
    _parser("help").parse_args(argv)
    rows = [("command", "description")]
    for command, description in COMMANDS.items():
        rows.append((f"uv run {command}", description))
        parser = _parser(command)
        formatter = parser._get_formatter()
        for action in parser._actions:
            if action.help != argparse.SUPPRESS:
                rows.append((f"  {formatter._format_action_invocation(action)}",
                             formatter._expand_help(action)))
    widths = [max(len(row[i]) for row in rows) for i in range(2)]
    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    lines = [border]
    for index, row in enumerate(rows):
        if index > 1 and row[0].startswith("uv run "):
            lines.append(border)
        lines.append("| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths)) + " |")
        if index == 0:
            lines.append(border)
    lines.append(border)
    print("\n".join(lines))
    return 0


def _main(feature, run, argv):
    args = _parser(feature).parse_args(argv)
    try:
        from .config import load_config

        config = load_config(args.config)
        paths = run(config)
    except (ValueError, OSError) as exc:
        print(f"{feature}: {exc}", file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


def main(argv=None):
    from .thrpt import run

    return _main("thrpt", run, argv)


def speedup_main(argv=None):
    from .speedup import run

    return _main("speedup", run, argv)


def gc_main(argv=None):
    from .gc_metrics import run

    return _main("gc", run, argv)


def compare_main(argv=None):
    from .compare import run

    return _main("compare", run, argv)
