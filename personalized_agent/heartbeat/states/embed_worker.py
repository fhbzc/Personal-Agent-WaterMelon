"""The embedding model, in a process of its own.

ON ONNX RUNTIME (the user, 2026-11-16: "你验证一下方案不影响之前的向量，就可以改"). It ran on torch and
transformers: 450-730 MB while loaded and 20-30 s to load, a large part of the installer, for a few
seconds' use an hour. The same model - all-MiniLM-L6-v2, its own ONNX file from the same repository, the
same revision - read by onnxruntime with the same tokeniser gives the same vectors: measured against the
2,826 the app had stored, the least alike was 0.9999998, no pair of states moved across the floor, and
every state's ten nearest stayed the same, in the same order. So nothing kept is read again and nothing
is grouped anew.

It still runs here, in a child started when a state round or a project's processing begins and ended
when it finishes - and the memory goes with it.

Run as a script, by its path, so it needs nothing from the package - only
the standard library and the three it exists to load. One request a line on
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
#: THE REVISION THE VECTORS WERE MEASURED ON (2026-11-16): every copy reads the very model its stored vectors
#: came from. Another model named in the settings is read as its repository has it now.
REVISIONS = {"sentence-transformers/all-MiniLM-L6-v2": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"}
#: The model's file, and the tokeniser's, in the repository.
ONNX_FILE = "onnx/model.onnx"
TOKENISER_FILE = "tokenizer.json"
#: ONNX Runtime's memory arena keeps what it once needed for the next batch; off, a batch takes what it needs
#: and gives it back.
MEMORY_ARENA = False
#: CARRIED IN THE INSTALLER (the user, 2026-11-16: "把这个打包进安装包"): a copy that cannot reach Hugging Face -
#: offline, or where the site is blocked - would otherwise compare words instead, quietly. The build puts the
#: pinned model's files here, each checked against its SHA-256; the worker reads them before it downloads.
BUNDLED_FOLDER = "models"
BUNDLED_FILES = {
    "sentence-transformers/all-MiniLM-L6-v2": {
        ONNX_FILE: "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
        TOKENISER_FILE: "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
    },
}


def threads() -> int:
    """How many threads the model may use: half the computer's, one at least."""
    return max(1, (os.cpu_count() or 2) // 2)


def bundled_folder(model_name: str, root: str) -> str:
    """Where a build keeps a pinned model's files under ``root``: by the model's own name, the files side by
    side. Short on purpose - the revision is pinned by each file's SHA-256, and a long path under a deep
    install folder passes Windows' 260 characters (the first real build stopped there, 2026-11-16)."""
    return os.path.join(root, BUNDLED_FOLDER, model_name.rsplit("/", 1)[-1])


def bundled_name(name: str) -> str:
    """A repository file's name in the carried folder: onnx/model.onnx is model.onnx."""
    return name.rsplit("/", 1)[-1]


def bundled_root():
    """The packaged application's own files, or None when run from source."""
    return getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None


def bundled(model_name: str, root=None):
    """{file in the repository: its path} carried with the application, or None when any is missing."""
    root = root if root is not None else bundled_root()
    if not root or model_name not in BUNDLED_FILES or model_name not in REVISIONS:
        return None
    folder = bundled_folder(model_name, root)
    found = {name: os.path.join(folder, bundled_name(name)) for name in BUNDLED_FILES[model_name]}
    return found if all(os.path.isfile(path) for path in found.values()) else None


def _open(tokeniser_path: str, model_path: str):
    import onnxruntime
    from tokenizers import Tokenizer

    tokeniser = Tokenizer.from_file(tokeniser_path)
    tokeniser.enable_truncation(MAX_TOKENS)
    tokeniser.enable_padding()
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = threads()
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = MEMORY_ARENA
    options.log_severity_level = 3
    session = onnxruntime.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])
    return tokeniser, session


def load(model_name: str, root=None):
    """The tokeniser and the model's session - from the files the application carries, or else each from
    the Hugging Face cache, downloaded once."""
    carried = bundled(model_name, root)
    if carried:
        try:
            return _open(carried[TOKENISER_FILE], carried[ONNX_FILE])
        except Exception as exc:     # a damaged copy: downloaded as before
            print(f"the carried model did not open ({type(exc).__name__}); downloading it", file=sys.stderr)
    from huggingface_hub import hf_hub_download

    revision = REVISIONS.get(model_name)
    return _open(hf_hub_download(model_name, TOKENISER_FILE, revision=revision),
                 hf_hub_download(model_name, ONNX_FILE, revision=revision))


def vectors(tokeniser, session, texts):
    """Each text as unit-length float32 numbers: the mean over the tokens that are not padding, then scaled to
    unit length - what this model was trained to be compared as, and what makes a dot product a cosine."""
    import numpy

    encoded = tokeniser.encode_batch(texts)
    mask = numpy.array([one.attention_mask for one in encoded], dtype=numpy.int64)
    feeds = {"input_ids": numpy.array([one.ids for one in encoded], dtype=numpy.int64), "attention_mask": mask}
    if "token_type_ids" in {one.name for one in session.get_inputs()}:
        feeds["token_type_ids"] = numpy.array([one.type_ids for one in encoded], dtype=numpy.int64)
    hidden = session.run(None, feeds)[0]
    weights = mask[..., None].astype(numpy.float32)
    pooled = (hidden * weights).sum(1) / numpy.clip(weights.sum(1), 1e-9, None)
    pooled /= numpy.clip(numpy.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
    return pooled.astype(numpy.float32)


def step_aside() -> None:
    """IN THE BACKGROUND, NOT IN THE USER'S WAY (the user, 2026-11-15: "我刚刚chat突然很卡" - each load took every
    core for 10-37 s, the chat with it): below normal priority, and half the computer's threads at most -
    said before the model's library is imported, which reads the thread counts then."""
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
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        tokeniser, session = load(model_name)
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
            rows = vectors(tokeniser, session, texts)
            say({"vectors": base64.b64encode(rows.tobytes()).decode("ascii"),
                 "n": int(rows.shape[0]), "dims": int(rows.shape[1]) if rows.shape[0] else 0})
        except Exception as exc:
            say({"error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return 0


if __name__ == "__main__":
    sys.exit(serve(sys.argv[1] if len(sys.argv) > 1 else
                   "sentence-transformers/all-MiniLM-L6-v2"))
