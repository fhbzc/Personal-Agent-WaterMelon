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


def serve(model_name: str) -> int:
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
