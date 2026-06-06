"""
Hermes CLI entry point.
Usage:
    python -m hermes chat                # Interactive chat
    python -m hermes serve               # Start web server
    python -m hermes task "do X"         # Autonomous plan-and-execute
    python -m hermes test                # Run self-tests
    python -m hermes status              # Show system status
    python -m hermes ingest <file>       # Add to knowledge base
    python -m hermes remember <text>     # Add to memory
"""
import asyncio
import os
import sys
import argparse


def main():
    parser = argparse.ArgumentParser(
        prog="hermes",
        description="Hermes - 赛博游民数字管家",
    )
    sub = parser.add_subparsers(dest="cmd")

    # chat
    sub.add_parser("chat", help="Interactive chat mode (CLI)")

    # serve
    serve = sub.add_parser("serve", help="Start web UI server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=7860)

    # task (autonomous)
    task = sub.add_parser("task", help="Autonomous plan-and-execute a goal")
    task.add_argument("goal", nargs="+", help="What to accomplish")
    task.add_argument("--mock", action="store_true",
                      help="Use mock LLM (for testing without GPU/API)")
    task.add_argument("--json", action="store_true",
                      help="Output result as JSON instead of formatted text")

    # test
    sub.add_parser("test", help="Run self-tests")

    # status
    sub.add_parser("status", help="Show system status")

    # ingest
    ingest = sub.add_parser("ingest", help="Add document to knowledge base")
    ingest.add_argument("path", help="File or directory to ingest")
    ingest.add_argument("--tag", default=None, help="Optional tag")

    # remember
    remember = sub.add_parser("remember", help="Add to long-term memory")
    remember.add_argument("text", help="Text to remember")

    # config
    cfg = sub.add_parser("config", help="Show resolved config")
    cfg.add_argument("--section", default=None)

    # download (alias for setup-model)
    sub.add_parser("download", help="Show model download instructions")

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        sys.exit(1)

    # Lazy import
    from hermes.config import load_config

    if args.cmd == "config":
        from hermes.config import load_config
        cfg = load_config()
        import json
        if args.section:
            data = getattr(cfg, args.section, None)
            if data is None:
                print(f"Section not found: {args.section}")
                sys.exit(1)
            print(json.dumps(data.model_dump() if hasattr(data, "model_dump") else data, indent=2, default=str, ensure_ascii=False))
        else:
            print(json.dumps(cfg.model_dump(), indent=2, default=str, ensure_ascii=False))
        return

    # All other commands need config + agent
    from hermes.agent import HermesAgent
    use_mock = getattr(args, 'mock', False)
    if args.cmd == "task":
        use_mock = use_mock or os.environ.get("HERMES_LLM_MOCK") == "1"
    agent = HermesAgent(load_config(), use_mock=use_mock)

    if args.cmd == "chat":
        agent.cli_chat()
    elif args.cmd == "serve":
        agent.start_server(host=args.host, port=args.port)
    elif args.cmd == "task":
        import json as _json
        goal = " ".join(args.goal)
        result = asyncio.run(agent.run_task(goal))
        if args.json:
            print(_json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            print()
            print(f"Goal: {result.goal}")
            print(f"Success: {result.success}  Iterations: {result.iterations}  Duration: {result.ended_at - result.started_at:.1f}s")
            print()
            print("Plan:")
            for s in result.plan:
                icon = "✓" if s.status == "ok" else "✗" if s.status == "failed" else "○"
                print(f"  {icon} step {s.step} [{s.skill}] {s.why}")
                if s.result:
                    preview = s.result[:150].replace("\n", " ")
                    print(f"     -> {preview}{'...' if len(s.result) > 150 else ''}")
                if s.error:
                    print(f"     ERR: {s.error[:200]}")
            print()
            print("Summary:")
            print(f"  {result.final}")
        sys.exit(0 if result.success else 2)
    elif args.cmd == "test":
        from hermes.tests import run_all_tests
        sys.exit(0 if run_all_tests(agent) else 1)
    elif args.cmd == "status":
        agent.print_status()
    elif args.cmd == "ingest":
        count = agent.knowledge.ingest(args.path, tag=args.tag)
        print(f"✓ Ingested {count} chunks from {args.path}")
    elif args.cmd == "remember":
        agent.memory.remember(args.text)
        print(f"✓ Remembered: {args.text[:80]}...")
    elif args.cmd == "download":
        print("See scripts/setup-model.sh / .bat for model download.")


if __name__ == "__main__":
    main()
