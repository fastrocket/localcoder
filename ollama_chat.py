#!/usr/bin/env python3
"""Code-focused Ollama client with auto-execution, retry loop, and program storage."""

import requests
import json
import re
import subprocess
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

OLLAMA_URL = "http://localhost:11434"
VENV_PYTHON = Path(__file__).parent / "venv" / "bin" / "python"
MAX_RETRIES = 5
MAX_CONTEXT = 16000
DB_PATH = Path(__file__).parent / "programs.db"

SYSTEM_PROMPT = """You are a code generation assistant. Your task is to generate working Python code.

RULES:
1. Emit ONLY Python code wrapped in triple backticks (```python ... ```)
2. NO explanations, NO markdown text outside code blocks
3. NO comments unless absolutely necessary for complex logic
4. Write complete, runnable Python scripts
5. Handle errors gracefully
6. Use standard library only unless user explicitly requests otherwise

Your response should contain ONLY the code block, nothing else."""


def print_help():
    """Print available commands."""
    print("""
Available commands:
  /help or /?     Show this help message
  /list           List saved programs (truncated view)
  /list <n>       Show full details of program #n
  /run <n>        Run saved program #n
  /delete <n>     Delete saved program #n
  quit or q       Exit the program

Any other input is treated as a code request to the LLM.
""")


def print_intro_example():
    """Print a quick intro with an example request."""
    print("""
Example request:
  > write a python program which takes two optional args n m, with
    defaults to n = m = 10, and then run it

The LLM will generate code like:
  ```python
  import sys
  def main(n=10, m=10):
      print(f"n={n}, m={m}")
  if __name__ == '__main__':
      n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
      m = int(sys.argv[2]) if len(sys.argv) > 2 else 10
      main(n, m)
  ```

The code executes automatically. If it fails, the error is sent back
for the LLM to fix (up to 5 retries). After success, you can save it.
""")


def init_database():
    """Initialize SQLite database for storing programs."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt TEXT NOT NULL,
            code TEXT NOT NULL,
            output TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def save_program(prompt, code, output):
    """Save a program to the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO programs (prompt, code, output) VALUES (?, ?, ?)",
        (prompt, code, output)
    )
    program_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return program_id


def list_programs():
    """List all saved programs (truncated)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, prompt, code, output, created_at FROM programs ORDER BY id")
    programs = cursor.fetchall()
    conn.close()
    
    if not programs:
        print("No saved programs found.")
        return
    
    print(f"\n{'='*60}")
    print(f"{'ID':<5} {'Prompt (truncated)':<30} {'Code (truncated)':<20}")
    print(f"{'-'*60}")
    
    for prog in programs:
        pid, prompt, code, output, created = prog
        prompt_short = prompt[:27] + "..." if len(prompt) > 30 else prompt
        code_short = code[:17] + "..." if len(code) > 20 else code
        print(f"{pid:<5} {prompt_short:<30} {code_short:<20}")
    
    print(f"{'='*60}")
    print(f"Use /list <n> to see full details, /run <n> to execute")


def get_program(program_id):
    """Get full details of a specific program."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, prompt, code, output, created_at FROM programs WHERE id = ?",
        (program_id,)
    )
    program = cursor.fetchone()
    conn.close()
    return program


def show_program_details(program_id):
    """Display full details of a program."""
    prog = get_program(program_id)
    if not prog:
        print(f"Program #{program_id} not found.")
        return
    
    pid, prompt, code, output, created = prog
    print(f"\n{'='*60}")
    print(f"Program #{pid} (saved: {created})")
    print(f"{'='*60}")
    print(f"\nPROMPT:\n{prompt}")
    print(f"\n{'-'*40}")
    print(f"CODE:\n```python\n{code}\n```")
    print(f"\n{'-'*40}")
    print(f"OUTPUT:\n{output}")
    print(f"{'='*60}")


def delete_program(program_id):
    """Delete a program from the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM programs WHERE id = ?", (program_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    
    if deleted:
        print(f"Program #{program_id} deleted.")
    else:
        print(f"Program #{program_id} not found.")
    return deleted


def run_saved_program(program_id):
    """Execute a saved program."""
    prog = get_program(program_id)
    if not prog:
        print(f"Program #{program_id} not found.")
        return
    
    _, prompt, code, output, created = prog
    print(f"\nRunning Program #{program_id}...")
    print(f"Original prompt: {prompt[:50]}...")
    print("-" * 40)
    
    success, new_output = run_python_code(code)
    print(new_output)
    print("-" * 40)
    
    if success:
        print("[SUCCESS]")
    else:
        print("[FAILED]")


def extract_python_code(text):
    """Extract Python code blocks from response."""
    pattern = r'```(?:python)?\n(.*?)\n```'
    matches = re.findall(pattern, text, re.DOTALL)
    return matches[0] if matches else None


def run_python_code(code):
    """Execute Python code and return (success, output)."""
    python_path = VENV_PYTHON if VENV_PYTHON.exists() else sys.executable
    try:
        result = subprocess.run(
            [str(python_path), "-c", code],
            capture_output=True,
            text=True,
            timeout=30
        )
        success = result.returncode == 0
        output = result.stdout if success else f"Error (exit {result.returncode}):\n{result.stderr}"
        return success, output.strip()
    except subprocess.TimeoutExpired:
        return False, "Error: Code execution timed out after 30 seconds"
    except Exception as e:
        return False, f"Error executing code: {e}"


def truncate_messages(messages, max_chars=MAX_CONTEXT):
    """Keep messages under context limit, preserving system and most recent."""
    if not messages:
        return messages
    
    # Always keep system message
    system_msg = messages[0] if messages[0].get("role") == "system" else None
    
    # Keep most recent messages until limit
    kept = []
    current_len = 0
    
    for msg in reversed(messages):
        msg_len = len(msg.get("content", ""))
        if current_len + msg_len > max_chars and kept:
            break
        kept.append(msg)
        current_len += msg_len
    
    result = list(reversed(kept))
    if system_msg and (not result or result[0].get("role") != "system"):
        result.insert(0, system_msg)
    
    return result


def chat_request_stream(model, messages):
    """Send chat request and stream response while collecting full text."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True
    }
    
    full_response = ""
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            stream=True,
            timeout=120
        )
        response.raise_for_status()
        
        print(f"\n{model}: ", end="", flush=True)
        for line in response.iter_lines():
            if line:
                data = json.loads(line)
                if "message" in data and "content" in data["message"]:
                    chunk = data["message"]["content"]
                    full_response += chunk
                    print(chunk, end="", flush=True)
                if data.get("done", False):
                    break
        print()
        return full_response
        
    except requests.exceptions.Timeout:
        print("Error: Request timed out")
        return ""
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")
        return ""


def process_code_request(model, user_input, messages):
    """Generate code, execute, retry on failure. Returns (messages, code, output, success)."""
    # Add user request
    messages.append({"role": "user", "content": user_input})
    messages = truncate_messages(messages)
    
    consecutive_failures = 0
    total_attempts = 0
    last_code = None
    final_code = None
    final_output = None
    
    while True:
        if consecutive_failures >= MAX_RETRIES:
            print(f"\n[Failed {MAX_RETRIES} consecutive attempts]")
            cont = input("Continue trying? [y/N]: ").strip().lower()
            if cont not in ("y", "yes"):
                return messages, final_code, final_output, False
            consecutive_failures = 0
            total_attempts = 0
        
        total_attempts += 1
        print(f"\n[Attempt {total_attempts}, failures: {consecutive_failures}]")
        
        # Get code from LLM (streamed)
        response = chat_request_stream(model, messages)
        
        if not response:
            print("No response received")
            consecutive_failures += 1
            continue
        
        code = extract_python_code(response)
        if not code:
            print("No code block found in response")
            error_msg = "ERROR: No code block found. Emit ONLY code wrapped in ```python ... ```"
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": error_msg})
            consecutive_failures += 1
            continue
        
        # Don't retry identical code
        if code == last_code:
            print("Same code as previous attempt, not retrying")
            messages.append({"role": "assistant", "content": response})
            consecutive_failures += 1
            continue
        
        last_code = code
        final_code = code
        print(f"\n[Executing code...]")
        print("-" * 40)
        
        success, output = run_python_code(code)
        final_output = output
        
        print(output)
        print("-" * 40)
        
        messages.append({"role": "assistant", "content": response})
        
        if success:
            print("[SUCCESS]")
            return messages, code, output, True
        
        # Failed - send error back for retry
        print("[FAILED - sending error to LLM for fix]")
        retry_prompt = f"""The code failed with this error:

{output}

Here is the code that failed:

```python
{code}
```

Fix the error and provide corrected code. Remember: ONLY emit the fixed code block, no explanations."""
        
        messages.append({"role": "user", "content": retry_prompt})
        messages = truncate_messages(messages)
        consecutive_failures += 1


def list_models():
    """Fetch available models."""
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        data = response.json()
        return [model["name"] for model in data.get("models", [])]
    except requests.exceptions.ConnectionError:
        print("Error: Cannot connect to Ollama. Is it running?")
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error fetching models: {e}")
        return []


def select_model(models):
    """Let user pick a model."""
    if not models:
        print("No models found. Pull a model first with: ollama pull <model>")
        return None

    print("\nAvailable models:")
    for i, model in enumerate(models, 1):
        print(f"  {i}. {model}")

    while True:
        try:
            choice = input("\nSelect model (number): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                return models[idx]
            print("Invalid selection.")
        except ValueError:
            print("Please enter a number.")


def main():
    # Initialize database
    init_database()
    
    # Print help at startup
    print_help()
    
    models = list_models()
    model = select_model(models)
    if not model:
        return

    # Print config info
    python_path = VENV_PYTHON if VENV_PYTHON.exists() else sys.executable
    print(f"\n{'='*50}")
    print(f"Model: {model}")
    print(f"Python: {python_path}")
    print(f"Max retries: {MAX_RETRIES} (then ask to continue)")
    print(f"Context limit: ~{MAX_CONTEXT} chars")
    print(f"Auto-execution: ENABLED")
    print(f"Database: {DB_PATH}")
    print(f"{'='*50}")
    print_intro_example()
    print("\nEnter your code request. Type 'quit' to exit.\n")

    # Initialize conversation with system prompt
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("> ").strip()
            if not user_input:
                continue
            
            # Check for commands (intercept before LLM)
            if user_input.lower() in ("/help", "/?"):
                print_help()
                continue
            
            if user_input.lower() == "/list":
                list_programs()
                continue
            
            if user_input.lower().startswith("/list "):
                try:
                    prog_id = int(user_input.split()[1])
                    show_program_details(prog_id)
                except (ValueError, IndexError):
                    print("Usage: /list <n>")
                continue
            
            if user_input.lower().startswith("/run "):
                try:
                    prog_id = int(user_input.split()[1])
                    run_saved_program(prog_id)
                except (ValueError, IndexError):
                    print("Usage: /run <n>")
                continue
            
            if user_input.lower().startswith("/delete "):
                try:
                    prog_id = int(user_input.split()[1])
                    delete_program(prog_id)
                except (ValueError, IndexError):
                    print("Usage: /delete <n>")
                continue
            
            if user_input.lower() in ("quit", "exit", "q"):
                break
            
            # Process as code request
            messages, code, output, success = process_code_request(model, user_input, messages)
            
            # After success, prompt to save
            if success and code:
                save = input("\nSave this program? [y/N]: ").strip().lower()
                if save in ("y", "yes"):
                    prog_id = save_program(user_input, code, output)
                    print(f"Program saved as #{prog_id}")

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break


if __name__ == "__main__":
    main()
