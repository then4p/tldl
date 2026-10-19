"""Model load benchmark, see model-loading.md.

Run inside the bot container, one fresh process per measurement:
    docker compose exec -T -e HF_HUB_OFFLINE=1 tldl python /app/data/bench_load.py parakeet
(copy this file to data/ first). Optional second argument: an ONNX Runtime graph
optimization level (all, extended, basic, none) or "nospin".
Prints JSON: import time, load time, Hugging Face lookup time, time per ONNX
session, and for Parakeet one 30 s inference on noise.
"""
import os, sys, time, json
t0 = time.perf_counter()

import huggingface_hub, onnxruntime as ort
timings = {"hub": 0.0, "sessions": []}

# time Hugging Face lookups
for name in ("snapshot_download", "hf_hub_download"):
    orig = getattr(huggingface_hub, name)
    def wrapped(*a, _orig=orig, **k):
        t = time.perf_counter(); r = _orig(*a, **k); timings["hub"] += time.perf_counter() - t; return r
    setattr(huggingface_hub, name, wrapped)

# time each ONNX Runtime session, optionally changing the graph optimization level
opt = sys.argv[2] if len(sys.argv) > 2 else None
levels = {"all": ort.GraphOptimizationLevel.ORT_ENABLE_ALL, "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
          "basic": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC, "none": ort.GraphOptimizationLevel.ORT_DISABLE_ALL}
orig_init = ort.InferenceSession.__init__
def init(self, path, sess_options=None, *a, **k):
    if opt and opt in levels:
        sess_options = sess_options or ort.SessionOptions()
        sess_options.graph_optimization_level = levels[opt]
    if opt == "nospin":
        sess_options = sess_options or ort.SessionOptions()
        sess_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        sess_options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    t = time.perf_counter(); orig_init(self, path, sess_options, *a, **k)
    timings["sessions"].append((os.path.basename(str(path)), round(time.perf_counter() - t, 2)))
ort.InferenceSession.__init__ = init

import asyncio
import numpy as np
from tldl.engines.onnx import OnnxEngine
t_import = time.perf_counter() - t0

kind = sys.argv[1]
kw = {"threads": 5}
if kind == "canary":
    kw |= {"model": "nemo-canary-1b-v2", "detect_language": "onnx-community/whisper-base"}
engine = OnnxEngine(kind, **kw)

async def main():
    t = time.perf_counter(); await engine.ensure_loaded(); load = time.perf_counter() - t
    infer = None
    if kind == "parakeet":  # one 30 s inference, to see what optimization levels cost at runtime
        x = (np.random.default_rng(0).standard_normal(30 * 16000) * 0.05).astype(np.float32)
        t = time.perf_counter(); await asyncio.to_thread(engine._run, x, None); infer = time.perf_counter() - t
    timings["sessions"] = [(n if n.endswith(".onnx") else "(in-memory)", t) for n, t in timings["sessions"]]
    print(json.dumps({"import": round(t_import, 2), "load": round(load, 2), "hub": round(timings["hub"], 2),
                      "sessions": timings["sessions"], "infer30s": infer and round(infer, 2)}))
asyncio.run(main())
