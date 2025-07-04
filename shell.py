#!/usr/bin/env python3
import os
import shutil
import subprocess
import re
import readline
import getpass
import socket
import shlex
from typing import List, Optional
from colorama import init, Fore, Style

init(autoreset=True)

HISTFILE = os.path.expanduser("~/.shistory")
HISTORY_LIMIT = 1000

user_vars = {}

def load_history() -> None:
    try:
        readline.read_history_file(HISTFILE)
        readline.set_history_length(HISTORY_LIMIT)
    except FileNotFoundError:
        pass

def save_history() -> None:
    try:
        readline.write_history_file(HISTFILE)
    except Exception:
        pass

def expand_vars(command: str) -> str:
    pattern = re.compile(r'\$(\w+)')
    def replace_var(match):
        name = match.group(1)
        return user_vars.get(name) or os.environ.get(name, "")
    return pattern.sub(replace_var, command)

def assign_variable(line: str) -> bool:
    if '=' not in line or line.strip().startswith('export '):
        return False
    var, _, val = line.partition('=')
    var = var.strip()
    val = val.strip()
    if var.isidentifier():
        user_vars[var] = val
        return True
    return False

def internal_help(args: List[str]) -> int:
    cmds = sorted([c for c in internal_commands if c != 'set'])
    help_text = Fore.CYAN + "Internal commands:\n"
    help_text += "\n".join(f" - {c}" for c in cmds)
    help_text += "\n\nAliases:\n - ld (ls)\n - e (echo)"
    help_text += "\n\nSupports variable expansion ($VAR), assignments VAR=val, "
    help_text += "command chaining with ';', '&&', and pipes '|'"
    print(help_text)
    return 0

def internal_exit(args: List[str]) -> int:
    print("Exiting TrashShell...")
    raise SystemExit

def internal_clear(args: List[str]) -> int:
    os.system('cls' if os.name == 'nt' else 'clear')
    return 0

def internal_env(args: List[str]) -> int:
    merged = {**os.environ, **user_vars}
    for k, v in merged.items():
        print(f"{k}={v}")
    return 0

def internal_set(args: List[str]) -> int:
    if len(args) >= 2 and args[0].isidentifier():
        user_vars[args[0]] = " ".join(args[1:])
    return 0

internal_commands = {
    "help": internal_help,
    "exit": internal_exit,
    "clear": internal_clear,
    "env": internal_env,
    "set": internal_set,
    "cd": internal_cd,
    "ld": internal_ls,
    "e": internal_echo,
}

def internal_cd(args: List[str]) -> int:
    if not args:
        path = os.path.expanduser("~")
    elif len(args) == 1:
        path = args[0]
    else:
        print(Fore.RED + "cd: too many arguments")
        return 1

    try:
        os.chdir(os.path.expanduser(path))
        return 0
    except FileNotFoundError:
        print(Fore.RED + f"cd: no such file or directory: {path}")
        return 1
    except Exception as e:
        print(Fore.RED + f"cd: {e}")
        return 1

def internal_ls(args: List[str]) -> int:
    path = args[0] if args else "."
    try:
        for entry in os.listdir(os.path.expanduser(path)):
            print(entry)
        return 0
    except FileNotFoundError:
        print(Fore.RED + f"ls: cannot access '{path}': No such file or directory")
        return 1
    except Exception as e:
        print(Fore.RED + f"ls: {e}")
        return 1

def internal_echo(args: List[str]) -> int:
    print(" ".join(args))
    return 0

def execute_internal(cmd: str, args: List[str]) -> int:
    try:
        return internal_commands[cmd](args)
    except SystemExit:
        raise
    except Exception as e:
        print(Fore.RED + f"Error in internal command '{cmd}': {e}")
        return 1

def execute_external(cmd: str, args: List[str], stdin=None, stdout=None) -> int:
    try:
        proc = subprocess.Popen(
            [cmd] + args, stdin=stdin, stdout=stdout
        )
        proc.wait()
        return proc.returncode
    except Exception as e:
        print(Fore.RED + f"Error executing '{cmd}': {e}")
        return 1

def run_command(line: str, stdin=None, stdout=None) -> int:
    line = line.strip()
    if not line:
        return 0
    if assign_variable(line):
        return 0

    line = expand_vars(line)
    parts = line.split()
    if not parts:
        return 0
    cmd, args = parts[0], parts[1:]

    if cmd in internal_commands:
        return execute_internal(cmd, args)

    path = shutil.which(cmd)
    if path:
        return execute_external(path, args, stdin=stdin, stdout=stdout)

    print(Fore.RED + Style.BRIGHT +
          f"Command not found: '{cmd}'")
    return 127

def run_pipeline(line: str) -> int:
    commands = [cmd.strip() for cmd in line.split('|')]
    if not commands:
        return 0

    procs = []
    prev_stdout = None

    for i, cmd in enumerate(commands):
        first_word = cmd.split()[0] if cmd else ""
        if first_word in internal_commands:
            print(Fore.RED + "Internal commands cannot be used in pipelines.")
            for p in procs:
                p.kill()
            return 1

        stdin = prev_stdout
        stdout = subprocess.PIPE if i < len(commands) - 1 else None

        try:
            proc = subprocess.Popen(
                cmd, shell=True, stdin=stdin, stdout=stdout
            )
        except Exception as e:
            print(Fore.RED + f"Pipeline execution failed: {e}")
            for p in procs:
                p.kill()
            return 1

        if prev_stdout:
            prev_stdout.close()
        prev_stdout = proc.stdout
        procs.append(proc)

    retcodes = []
    for p in procs:
        p.wait()
        retcodes.append(p.returncode)

    return retcodes[-1] if retcodes else 0

def execute_line(line: str) -> None:
    tokens = shlex.split(line, posix=True)
    if not tokens:
        return

    segments = []
    current = []
    operators = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ['&&', '||', ';']:
            segments.append(' '.join(current))
            current = []
            operators.append(token)
        else:
            current.append(token)
        i += 1
    if current:
        segments.append(' '.join(current))

    assert len(segments) >= 1

    proceed = True
    last_code = 0

    for idx, segment in enumerate(segments):
        if idx > 0:
            op = operators[idx - 1]
            if op == '&&' and last_code != 0:
                proceed = False
            elif op == '||' and last_code == 0:
                proceed = False
            else:
                proceed = True

        if not proceed:
            continue

        if '|' in segment:
            last_code = run_pipeline(segment)
        else:
            last_code = run_command(segment)

def shorten_cwd(path: str) -> str:
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return os.path.basename(path) or "/"

def prompt() -> str:
    user = getpass.getuser()
    host = socket.gethostname().split('.')[0]
    cwd = shorten_cwd(os.getcwd())
    return f"{Fore.BLUE}{user}@{host}:{cwd} >>> {Style.RESET_ALL}"

def main() -> None:
    load_history()
    print(Fore.GREEN + "TrashShell v1.5 - "
          "type 'help' for commands")

    try:
        while True:
            try:
                line = input(prompt())
                execute_line(line)
            except KeyboardInterrupt:
                print(Fore.YELLOW + "\n(To exit, type 'exit')")
            except EOFError:
                print("\nGoodbye.")
                break
            except SystemExit:
                break
            except Exception as e:
                print(Fore.RED + f"Unexpected error: {e}")
    finally:
        save_history()

if __name__ == "__main__":
    main()
