"""Exporta un checkpoint de Lightning (repo IdeoGraphCO) al artefacto del servicio.

    python -m inference.export_checkpoint \\
        --checkpoint ../IdeoGraphCO/logs/checkpoints/cribado/beto__seed42/best.ckpt \\
        --metrics ../IdeoGraphCO/logs/benchmark/cribado/beto__seed42/metrics.json \\
        --model-version beto-cribado-s42 --out models/beto-cribado-s42

El artefacto se describe a sí mismo (docs/traspaso-inferencia-BE.md):

- `encoder/`: configuración, pesos y tokenizador en formato de transformers.
- `head.safetensors`: la capa lineal de 8 salidas.
- `ideographco.json`: orden de clases, troceo, versión y procedencia.

El troceo sale de los hiperparámetros del checkpoint (regla 1) y el orden de
clases del `src/core/schema.py` del repo de ML (regla 3), así que el
predictor puede verificar ambos al cargar. Pesa unos 440 MB frente a los
1,3 GB del checkpoint, que además guarda el estado del optimizador.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import torch
from safetensors.torch import save_file
from transformers import AutoModel, AutoTokenizer

from inference.predictor import ARTIFACT_META


def ml_repo_classes(ml_repo: Path) -> list[str]:
    """Lee IDEOLOGY_CLASSES del repo de ML sin importar su código."""
    tree = ast.parse((ml_repo / "src" / "core" / "schema.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign | ast.Assign):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if any(getattr(t, "id", None) == "IDEOLOGY_CLASSES" for t in targets):
                return list(ast.literal_eval(node.value))
    raise ValueError(f"No se encontró IDEOLOGY_CLASSES en {ml_repo}/src/core/schema.py")


def git_commit(repo: Path) -> str | None:
    """Commit del repo de ML; `-dirty` si había cambios sin commit al exportar."""
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{commit}-dirty" if dirty else commit


def export(
    checkpoint: Path,
    out: Path,
    model_version: str,
    ml_repo: Path,
    metrics: Path | None = None,
) -> dict:
    ckpt = torch.load(checkpoint, map_location="cpu", weights_only=False)
    hp = dict(ckpt["hyper_parameters"])
    dm = dict(ckpt.get("datamodule_hyper_parameters") or {})
    if hp.get("use_politicity_head"):
        raise NotImplementedError("El servicio no expone la cabeza de politicidad")

    chunking = {key: int(hp[key]) for key in ("chunk_size", "chunk_stride", "max_chunks")}
    for key, value in chunking.items():
        if key in dm and int(dm[key]) != value:
            raise ValueError(f"{key}: modelo {value} ≠ datamodule {dm[key]} en el checkpoint")
    chunking["strategy"] = dm.get("chunking_strategy", "sliding_window")

    state = ckpt["state_dict"]
    encoder = AutoModel.from_pretrained(hp["model_name"])
    encoder.load_state_dict(
        {k.removeprefix("encoder."): v for k, v in state.items() if k.startswith("encoder.")},
        strict=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(hp["model_name"])

    out.mkdir(parents=True, exist_ok=True)
    encoder.save_pretrained(out / "encoder")
    tokenizer.save_pretrained(out / "encoder")
    save_file(
        {"weight": state["classifier.weight"].contiguous(), "bias": state["classifier.bias"].contiguous()},
        str(out / "head.safetensors"),
    )

    meta = {
        "model_version": model_version,
        "base_model": hp["model_name"],
        "classes": ml_repo_classes(ml_repo),
        "chunking": chunking,
        "source": {
            "checkpoint": str(checkpoint),
            "epoch": ckpt.get("epoch"),
            "ml_repo_commit": git_commit(ml_repo),
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    }
    if metrics:
        run = json.loads(metrics.read_text(encoding="utf-8"))
        meta["validation"] = {
            "f1_macro": run.get("best_val_score"),
            "metrics": run.get("val_metrics"),
            "seed": run.get("seed"),
        }
    (out / ARTIFACT_META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--ml-repo", type=Path, default=Path("../IdeoGraphCO"))
    parser.add_argument("--metrics", type=Path, default=None,
                        help="metrics.json de la corrida, para dejar el F1 de validación en el artefacto")
    args = parser.parse_args()
    meta = export(args.checkpoint, args.out, args.model_version, args.ml_repo, args.metrics)
    print(json.dumps({k: meta[k] for k in ("model_version", "base_model", "chunking")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
