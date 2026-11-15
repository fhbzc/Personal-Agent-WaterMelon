"""The embedding model, in a process of its own.

Torch and transformers cost the agent about 450 MB once imported, and an
import cannot be undone: a model loaded in the agent's own process stays
there until the agent quits, though it is used for a few seconds an hour.
So it is loaded here instead, in a child started when a state round or a
project's processing begins and ended when it finishes - and the memory
goes with it.

Run as a script, by its path, so it needs nothing from the package - only
the standard library and the two it exists to load. One request a line on
stdin, one answer a line on stdout:

    -> {"texts": ["...", ...]}
    <- {"vectors": "<base64 float32>", "n": 2, "dims": 384}

The first line it writes says whether the model loaded. End of input ends
it.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time

MAX_TOKENS = 256


def threads() -> int:
    """How many threads torch may use: half the computer's, one at least."""
    return max(1, (os.cpu_count() or 2) // 2)


def step_aside() -> None:
    """IN THE BACKGROUND, NOT IN THE USER'S WAY (the user, 2026-11-15: "我刚刚chat突然很卡" - each load took every
    core for 10-37 s, the chat with it): below normal priority, and half the computer's threads at most -
    said before torch is imported, which reads the thread counts then."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)   # BELOW_NORMAL_PRIORITY_CLASS
        else:
            os.nice(10)
    except Exception:            # pragma: no cover - left at its priority
        pass
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(name, str(threads()))


def serve(model_name: str) -> int:
    step_aside()
    # The answers are the only thing on stdout: anything a library prints
    # while loading goes to stderr instead.
    answer = sys.stdout
    sys.stdout = sys.stderr

    def say(message: dict) -> None:
        answer.write(json.dumps(message) + "\n")
        answer.flush()

    began = time.perf_counter()
    try:
        # Before the import: `transformers` decides then which tensor
        # library to reach for, and on a machine with TensorFlow too it
        # reaches for that one and fails.
        os.environ.setdefault("USE_TF", "0")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        import array

        import torch
        from transformers import AutoModel, AutoTokenizer

        torch.set_num_threads(threads())
        tokeniser = AutoTokenizer.from_pretrained(model_name)
        model = AutoModel.from_pretrained(model_name)
        model.eval()
    except Exception as exc:
        say({"error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        return 1
    say({"ready": True, "load_ms": int((time.perf_counter() - began) * 1000)})

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            texts = [str(one or "") for one in json.loads(line).get("texts") or []]
            if not texts:
                say({"vectors": "", "n": 0, "dims": 0})
                continue
            tokens = tokeniser(texts, padding=True, truncation=True,
                               max_length=MAX_TOKENS, return_tensors="pt")
            with torch.no_grad():
                hidden = model(**tokens).last_hidden_state
            # Mean over the tokens that are not padding, then scaled to
            # unit length - what this model was trained to be compared as,
            # and what makes a dot product a cosine.
            mask = tokens["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            rows = pooled.tolist()
            flat = array.array("f", [value for row in rows for value in row])
            say({"vectors": base64.b64encode(flat.tobytes()).decode("ascii"),
                 "n": len(rows), "dims": len(rows[0]) if rows else 0})
        except Exception as exc:
            say({"error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return 0


if __name__ == "__main__":
    sys.exit(serve(sys.argv[1] if len(sys.argv) > 1 else
                   "sentence-transformers/all-MiniLM-L6-v2"))
