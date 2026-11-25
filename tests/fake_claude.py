"""`claude -p` falso para las pruebas de la explicación en vivo.

Lo lanza un ejecutable `claude` de una línea que crean las pruebas (ver
`tests/test_explanation_stream.py`). Lee el plan de FAKE_CLAUDE_PLAN: un JSON con
un intento por llamada, en orden, y repite el último si se acaban:

    {"log": "<ruta>", "attempts": [{"lines": [...] | "lines_file": "<ruta>",
      "exit": 0, "stderr": "", "hang_after": null, "spawn_child": false}]}

Cada llamada anota en el log sus argumentos, lo que recibió por stdin y su pid,
y escribe las líneas de stream-json una a una. Con `hang_after: n`, después de
n líneas se queda esperando, como un modelo que razona: así se prueba que el
proceso muere cuando se cierra el stream o se agota el tiempo. Con
`spawn_child`, antes lanza un `sleep` que hereda sus pipes (anota su pid en
`child_pid`): así se prueba que se mata el grupo entero y no solo `claude`.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    plan_path = Path(os.environ["FAKE_CLAUDE_PLAN"])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    counter = plan_path.with_suffix(".count")
    call = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(call + 1))
    attempt = plan["attempts"][min(call, len(plan["attempts"]) - 1)]

    prompt = sys.stdin.read()
    entry = {"argv": sys.argv[1:], "stdin": prompt, "pid": os.getpid()}
    if attempt.get("spawn_child"):
        entry["child_pid"] = subprocess.Popen(["sleep", "60"]).pid
    with open(plan["log"], "a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")

    if "lines_file" in attempt:
        lines = Path(attempt["lines_file"]).read_text(encoding="utf-8").splitlines()
    else:
        lines = [json.dumps(line, ensure_ascii=False) for line in attempt["lines"]]
    hang_after = attempt.get("hang_after")
    for index, line in enumerate(lines):
        if hang_after is not None and index == hang_after:
            time.sleep(60)
        print(line, flush=True)
    if hang_after is not None and hang_after >= len(lines):
        time.sleep(60)
    if attempt.get("stderr"):
        print(attempt["stderr"], file=sys.stderr, flush=True)
    return int(attempt.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
