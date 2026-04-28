# LocalCoder

A code-focused Ollama client with auto-execution, retry loops, and program storage.

## Features

- **Auto-execution**: Code runs automatically after generation
- **Retry loop**: Failed code is sent back to the LLM with errors for fixing (up to 5 retries)
- **Program storage**: Save successful programs to SQLite database
- **Interactive commands**: List, view, run, and delete saved programs
- **Streaming responses**: Watch code being generated in real-time

## Requirements

- Python 3.7+
- Ollama running locally
- `requests` library

## Setup

```bash
pip install requests
```

## Usage

```bash
python ollama_chat.py
```

### Commands

| Command | Description |
|---------|-------------|
| `> calculate fibonacci(100)` | Send code request to LLM |
| `/list` | List saved programs (truncated) |
| `/list 1` | Show full details of program #1 |
| `/run 1` | Execute saved program #1 |
| `/delete 1` | Delete program #1 |
| `/help` or `/?` | Show help |
| `quit` or `q` | Exit |

## How It Works

1. Select an Ollama model
2. Enter a code request (e.g., "calculate the 50th fibonacci number")
3. Code is generated and automatically executed
4. If it fails, the error is sent back for retry (up to 5 times)
5. After success, optionally save the program to the database

## Database

Programs are stored in `programs.db` (SQLite) with:
- Prompt
- Generated code
- Output
- Timestamp

## Safety

- Only connects to local Ollama (`localhost:11434`)
- Code executes in subprocess with 30-second timeout
- Uses `venv/bin/python` if available, otherwise system Python

## License

MIT
