"""
Ikaros TTS Worker — minimal subprocess for edge-tts only.

Protocol:
  Rust → Worker (stdin JSON line):
    {"text": "...", "voice": "zh-CN-XiaoxiaoNeural"}

  Worker → Rust (stdout binary):
    Raw MP3 bytes (all chunks concatenated)

  Worker → Rust (stderr):
    Progress/error log lines

Exit code 0 = success, non-zero = failure.
"""
import asyncio
import json
import sys


async def main():
    """Read one JSON line from stdin, stream TTS audio to stdout."""
    try:
        line = sys.stdin.readline().strip()
        if not line:
            print("no input", file=sys.stderr)
            sys.exit(1)

        msg = json.loads(line)
        text = msg.get("text", "")
        voice = msg.get("voice", "zh-CN-XiaoxiaoNeural")

        if not text:
            print("empty text", file=sys.stderr)
            sys.exit(1)

        import edge_tts

        communicate = edge_tts.Communicate(text, voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                sys.stdout.buffer.write(chunk["data"])
                sys.stdout.buffer.flush()

    except Exception as exc:
        print(f"TTS error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
