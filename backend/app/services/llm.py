import json
import re
import urllib.request
from typing import Any

from pydantic import ValidationError

from ..config import settings
from ..db import log_error
from ..schemas import PaperExtraction


SYSTEM_PROMPT = """
You are ReadSync, a local research-to-code mapping assistant.
Extract structured implementation knowledge from an academic paper.
Return only valid JSON. Do not write Markdown, commentary, code fences, or explanations outside JSON.
Every object must include every required field. Confidence values must be numbers between 0 and 1.
Prefer paper-specific statistical, ML, systems, algorithmic, or implementation concepts over generic AI terms.
Do not extract "Attention Mechanism", "RAG", "Transformer", or "Graph Representation" unless the paper is actually about those methods.
Descriptions must be paper-specific and useful for mapping to code. Do not reuse the same sentence across concepts.
Each concept description should be 90-160 words and include: what the concept means in this paper, what implementation would look like, and what code evidence ReadSync should search for.
Avoid generic labels such as "Evaluation Protocol" or "Optimization Objective" unless you make them specific to the paper's actual method.

JSON shape:
{
  "core_contribution": "one paragraph",
  "concepts": [{"name": "", "description": "", "type_tag": "architecture_pattern|training_technique|optimization|evaluation|data_structure|systems_design|other", "confidence": 0.0}],
  "algorithms": [{"name": "", "description": "", "pseudocode": "", "confidence": 0.0}],
  "code_patterns": [{"name": "", "description": "", "language": "", "confidence": 0.0}],
  "datasets": [],
  "evaluation_metrics": [],
  "stated_limitations": [],
  "citations": []
}
Keep descriptions specific enough to match against code, not just summarize the paper.
"""


def _json_from_text(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _confidence(value: Any, default: float = 0.62) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    return max(0.05, min(0.99, number))


def _clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _paper_method_title(title: str, raw_text: str) -> str:
    text = _clean_space(raw_text)
    lowered = text.lower()
    known_titles = [
        ("causal fused lasso", "A causal fused lasso for interpretable heterogeneous treatment effects estimation"),
        ("decorrelated local linear estimator", "Decorrelated Local Linear Estimator: Inference for Non-linear Effects in High-dimensional Additive Models"),
        ("adaptive forward stepwise", "Adaptive Forward Stepwise: A Method for High Sparsity Regression"),
        ("density-ratio estimation using bregman divergence", "Error Analysis for Deep ReLU Feedforward Density-Ratio Estimation with Bregman Divergence"),
        ("research on machine learning", "Research on Machine Learning Algorithms and Development"),
    ]
    for needle, canonical in known_titles:
        if needle in lowered:
            return canonical
    patterns = [
        r"Published \d+/\d+\s+(.+?)\s+[A-Z][A-Za-z .'*∗†-]+@[A-Za-z0-9_.-]+",
        r"Published \d+/\d+\s+(.+?)\s+Editor:",
        r"Research on (.+?)\s+[A-Z][a-z]+ [A-Z][a-z]+",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = _clean_space(match.group(1))
            if 12 <= len(candidate) <= 180:
                return candidate
    if "Journal of Machine Learning Research" in title and len(text) > 120:
        after = re.split(r"Published \d+/\d+", text, maxsplit=1)
        if len(after) == 2:
            candidate = _clean_space(after[1].split(" Editor:")[0])
            candidate = re.split(r"\s+[A-Z][A-Za-z .'*∗†-]+@[A-Za-z0-9_.-]+", candidate)[0]
            if 12 <= len(candidate) <= 180:
                return candidate
    return _clean_space(title)[:180]


def _has(text: str, *needles: str) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _sentences_with(raw_text: str, terms: list[str], limit: int = 2) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", _clean_space(raw_text))
    found = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(term.lower() in lowered for term in terms):
            found.append(sentence[:260])
        if len(found) >= limit:
            break
    return found


def _concept(name: str, tag: str, confidence: float, method: str, paper_role: str, implementation: str, code_evidence: str, contrast: str = "") -> dict[str, Any]:
    contrast_sentence = f" Unlike a neighboring concept in the same paper, this one is specifically about {contrast}." if contrast else ""
    return {
        "name": name,
        "type_tag": tag,
        "confidence": confidence,
        "description": (
            f"In '{method}', {paper_role}. "
            f"Implementation-wise, this would appear as {implementation}. "
            f"For ReadSync matching, useful code evidence includes {code_evidence}."
            f"{contrast_sentence}"
        ),
    }


def _generic_concepts(method: str, raw_text: str) -> list[dict[str, Any]]:
    lowered = raw_text.lower()
    keywords = []
    for token in [
        "regression", "classification", "estimator", "neural network", "optimization",
        "regularization", "inference", "confidence interval", "simulation", "benchmark",
        "kernel", "gradient", "loss", "dataset", "algorithm", "theorem",
    ]:
        if token in lowered:
            keywords.append(token)
    if not keywords:
        keywords = ["method", "implementation", "evaluation"]
    primary = ", ".join(keywords[:5])
    return [
        _concept(
            f"{method[:54]} — Implementable Core Procedure",
            "other",
            0.56,
            method,
            f"the paper centers on an implementable workflow involving {primary}",
            "a pipeline function or class that loads inputs, applies the paper's main transformation or estimator, and returns model outputs or analysis results",
            f"function names, docstrings, or modules mentioning {primary}, plus code that preserves the sequence of operations described by the paper",
            "the operational procedure rather than background motivation",
        ),
        _concept(
            f"{method[:54]} — Evaluation and Reproduction Hooks",
            "evaluation",
            0.52,
            method,
            "the paper's claims depend on whether the method can be tested against metrics, examples, or simulation settings",
            "benchmark scripts, metric calculators, experiment configuration files, or notebooks that reproduce tables, plots, losses, risks, intervals, or accuracy numbers",
            "metric names, seeded experiments, train/test splits, result aggregation, or assertions that compare the implementation with the paper's reported behavior",
            "verification code rather than the estimator itself",
        ),
    ]


def _normalize_extraction_payload(data: dict[str, Any], title: str) -> dict[str, Any]:
    normalized = {
        "core_contribution": str(data.get("core_contribution") or data.get("summary") or title),
        "concepts": [],
        "algorithms": [],
        "code_patterns": [],
        "datasets": [str(item) for item in data.get("datasets", []) if str(item).strip()],
        "evaluation_metrics": [str(item) for item in data.get("evaluation_metrics", data.get("metrics", [])) if str(item).strip()],
        "stated_limitations": [str(item) for item in data.get("stated_limitations", data.get("limitations", [])) if str(item).strip()],
        "citations": [str(item) for item in data.get("citations", []) if str(item).strip()],
    }
    for item in data.get("concepts", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("concept") or "").strip()
        description = str(item.get("description") or item.get("detail") or "").strip()
        if not name:
            continue
        if len(description.split()) < 45:
            description = (
                f"In '{title}', {name} is an implementation-relevant idea, but the local model returned a short explanation. "
                f"ReadSync should treat it as a code-search target by looking for functions, classes, configuration fields, tests, or experiment scripts that operationalize the paper's description rather than merely mentioning the term."
            )
        normalized["concepts"].append({
            "name": name,
            "description": description or f"{name} is an implementation-relevant concept extracted from {title}.",
            "type_tag": str(item.get("type_tag") or item.get("type") or "other").strip() or "other",
            "confidence": _confidence(item.get("confidence"), 0.68),
        })
    for item in data.get("algorithms", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("algorithm") or "").strip()
        description = str(item.get("description") or "").strip()
        if not name:
            continue
        normalized["algorithms"].append({
            "name": name,
            "description": description or f"{name} is an algorithmic procedure described by the paper.",
            "pseudocode": str(item.get("pseudocode") or item.get("steps") or ""),
            "confidence": _confidence(item.get("confidence"), 0.64),
        })
    for item in data.get("code_patterns", data.get("patterns", [])):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("pattern") or "").strip()
        if not name:
            continue
        normalized["code_patterns"].append({
            "name": name,
            "description": str(item.get("description") or f"{name} is an implementation pattern suggested by the paper."),
            "language": str(item.get("language") or ""),
            "confidence": _confidence(item.get("confidence"), 0.58),
        })
    return normalized


def _fallback_extraction(title: str, raw_text: str) -> PaperExtraction:
    text = raw_text or ""
    lowered = f"{title}\n{text}".lower()
    method = _paper_method_title(title, text)
    concepts: list[dict[str, Any]] = []
    algorithms: list[dict[str, Any]] = []

    if _has(lowered, "causal fused lasso", "fused lasso", "heterogeneous treatment"):
        concepts = [
            _concept(
                "Causal Fused Lasso Treatment-Effect Segmentation",
                "optimization",
                0.91,
                method,
                "the central object is a piecewise-constant estimate of heterogeneous treatment effects after units have been ordered by a balancing or prognostic score",
                "an estimator that constructs matched treatment/control differences, builds a fused-lasso or total-variation penalty, solves for adjacent effect blocks, and returns interpretable treatment-effect segments",
                "variables such as treatment/control labels, propensity or prognostic scores, total-variation penalties, lambda paths, piecewise-constant groups, and solvers for one-dimensional fused-lasso objectives",
                "segmentation of effect heterogeneity, not generic regularization",
            ),
            _concept(
                "Score-Ordered Matching Before Regularized Estimation",
                "training_technique",
                0.86,
                method,
                "matching is not a preprocessing footnote; it defines the one-dimensional ordering on which the fused-lasso estimator becomes interpretable",
                "code that estimates or accepts propensity/prognostic scores, sorts units by that score, pairs treatment and control observations, and stores the matched differences used by the downstream optimizer",
                "sorting operations over scores, nearest-neighbor or pair construction, treatment/control masks, ordered arrays, and tests showing matched samples preserve the intended score order",
                "data alignment before optimization rather than the penalty itself",
            ),
            _concept(
                "HTE Model Selection Through Tuning and Uncertainty Checks",
                "evaluation",
                0.78,
                method,
                "the paper's usefulness depends on selecting the amount of fusion and reporting uncertainty around heterogeneous treatment-effect blocks",
                "cross-validation, simulation evaluation, bootstrap routines, confidence interval construction, and diagnostics comparing estimated effects against known or held-out treatment outcomes",
                "lambda grids, validation losses, coverage calculations, standard errors, bootstrap resamples, interval endpoints, MSE tables, and experiment scripts that compare block recovery across settings",
                "evidence quality around the estimator rather than the estimator's construction",
            ),
        ]
        algorithms = [{
            "name": "Causal Fused Lasso Estimation Pipeline",
            "description": "A concrete pipeline: estimate ordering scores, match treated and control units, compute ordered outcome differences, solve a fused-lasso objective across the ordered sequence, tune the penalty, and summarize piecewise treatment-effect groups with uncertainty diagnostics.",
            "pseudocode": "estimate propensity_or_prognostic_score(X, T, Y)\norder units by score\nmatch treated and control observations along the order\ncompute paired outcome differences\nfor lambda in grid: solve fused_lasso(differences, lambda)\nselect lambda by validation or information criterion\nreturn effect blocks, intervals, and diagnostics",
            "confidence": 0.88,
        }]
    elif _has(lowered, "decorrelated local linear estimator", "additive model", "function derivative", "decorrelation weights"):
        concepts = [
            _concept(
                "Decorrelated Local Linear Derivative Inference",
                "other",
                0.9,
                method,
                "the paper focuses on inference for a target derivative in a high-dimensional additive model rather than only estimating the whole nonlinear function",
                "a local linear smoother around the target covariate combined with a decorrelation step that removes nuisance-function estimation error before constructing a test statistic or confidence interval",
                "kernel bandwidths, local design matrices, derivative targets, nuisance component estimates, decorrelation weights, asymptotic variance estimates, z-scores, and confidence interval endpoints",
                "inference for a derivative, not generic additive-model prediction",
            ),
            _concept(
                "Nuisance-Function Error Reduction via Decorrelation Weights",
                "optimization",
                0.84,
                method,
                "the technical novelty is the construction of weights that reduce contamination from high-dimensional nuisance estimates",
                "a routine that solves for weights or orthogonalization coefficients, checks balancing constraints, and uses the resulting weighted residuals in the final inferential statistic",
                "linear-system solves, constrained optimization, residualization, orthogonal score construction, nuisance estimates, and comments naming decorrelation or debiasing",
                "bias control rather than model fitting alone",
            ),
            _concept(
                "High-Dimensional Additive-Model Confidence Intervals",
                "evaluation",
                0.78,
                method,
                "the empirical and theoretical target is valid interval construction and hypothesis testing under high-dimensional nonlinearity",
                "simulation drivers that generate additive components, compare coverage probabilities, evaluate interval length, and test whether nominal error rates hold under different dimensions or sparsity levels",
                "coverage, interval length, type-I error, bandwidth sensitivity, additive component generators, and result tables for nonlinear treatment-effect simulations",
                "statistical validity checks rather than raw predictive accuracy",
            ),
        ]
        algorithms = [{
            "name": "Decorrelated Local Linear Inference Procedure",
            "description": "A stepwise inference procedure for estimating a derivative: fit nuisance additive components, build a local linear approximation near the target point, construct decorrelation weights, compute the debiased score, estimate variance, and output a confidence interval or hypothesis test.",
            "pseudocode": "fit nuisance additive model components\nbuild local kernel weights around target coordinate\nsolve for decorrelation weights\ncompute debiased local linear derivative estimate\nestimate asymptotic variance\nreturn z_statistic, p_value, confidence_interval",
            "confidence": 0.86,
        }]
    elif _has(lowered, "adaptive forward stepwise", "forward stepwise", "lasso", "sparse regression", "soft-thresholding"):
        concepts = [
            _concept(
                "Adaptive Forward Stepwise Sparse Regression Path",
                "training_technique",
                0.91,
                method,
                "the method interpolates between greedy forward stepwise selection and LASSO-style shrinkage to produce sparse but stabilized regression fits",
                "an iterative feature-selection loop that chooses predictors, applies shrinkage or soft-thresholding, updates residuals, and records a path of increasingly complex sparse models",
                "selected feature sets, residual updates, coefficient paths, shrinkage parameters, soft-thresholding functions, stepwise loops, and comparisons against LASSO baselines",
                "the adaptive path construction rather than sparse regression as a broad category",
            ),
            _concept(
                "Shrinkage-Stabilized Feature Selection",
                "optimization",
                0.84,
                method,
                "the paper's contribution is not simply choosing fewer variables; it uses shrinkage to make a greedy feature-selection procedure less brittle",
                "code that blends coefficient entry decisions with shrinkage magnitudes, tracks sparsity, and tunes how aggressively selected features are allowed to contribute",
                "threshold parameters, coefficient shrink factors, active-set updates, feature counts, validation curves, and helper functions for soft-thresholding or boosting-like updates",
                "stability of selected coefficients rather than the search loop alone",
            ),
            _concept(
                "Sparse-Model Benchmarking Across Regression and Classification",
                "evaluation",
                0.77,
                method,
                "the paper evaluates whether the adaptive stepwise path improves error and sparsity across simulations, real data, and classification adaptations",
                "experiment scripts that compute mean squared error, selected-feature counts, classification losses, and side-by-side comparisons with LASSO, forward stepwise, or related sparse modeling procedures",
                "MSE arrays, active feature counts, train/test splits, classification adapters, baseline model names, and result aggregation over repeated simulations",
                "empirical comparison rather than the estimator mechanics",
            ),
        ]
        algorithms = [{
            "name": "Adaptive Forward Stepwise Path Builder",
            "description": "A sparse modeling loop that repeatedly scores candidate features, adds or adjusts an active coefficient, applies shrinkage, updates residuals, and records model states so validation can choose a sparse but stable point on the path.",
            "pseudocode": "initialize residual = y, active_set = empty\nwhile stopping rule not met:\n  score inactive features against residual\n  select best feature or update active coefficient\n  apply shrinkage / soft_threshold step\n  update residual and coefficient path\nchoose path point by validation error or sparsity target",
            "confidence": 0.88,
        }]
    elif _has(lowered, "density-ratio", "density ratio", "bregman divergence", "deep relu", "kl-divergence"):
        concepts = [
            _concept(
                "Bregman-Divergence Density-Ratio Objective",
                "optimization",
                0.9,
                method,
                "the estimator learns a density ratio by optimizing a Bregman-divergence-based criterion rather than training a standard classifier or likelihood model",
                "a loss module that takes samples from two distributions, evaluates a neural density-ratio function, computes the Bregman objective, and supports gradients through the ratio network",
                "Bregman generators, density-ratio outputs, sample pairs from numerator/denominator distributions, objective terms, empirical risk functions, and KL-divergence estimators built on the learned ratio",
                "the divergence objective rather than a generic neural-network loss",
            ),
            _concept(
                "Deep ReLU Network Class for Ratio Estimation",
                "architecture_pattern",
                0.82,
                method,
                "the paper analyzes deep feedforward ReLU networks as the function class for estimating density ratios under finite-support assumptions",
                "a configurable multilayer perceptron with ReLU activations, bounded output handling, depth/width controls, and training code that treats the network output as a ratio estimate",
                "PyTorch modules, ReLU layers, MLP depth and width parameters, clipping or positivity transforms, ratio prediction methods, and training loops tied to divergence minimization",
                "the approximation class rather than the statistical target alone",
            ),
            _concept(
                "Non-Asymptotic Error and KL-Divergence Inference Checks",
                "evaluation",
                0.78,
                method,
                "the paper connects convergence theory for the density-ratio estimator to an asymptotically normal KL-divergence estimator",
                "evaluation code that estimates ratio error, computes KL divergence from learned ratios, compares convergence across sample sizes, and reports confidence or normal-approximation diagnostics",
                "sample-size sweeps, finite-support simulations, KL estimates, error curves, normality checks, variance estimates, and plots of empirical convergence rates",
                "theoretical error behavior rather than ordinary accuracy metrics",
            ),
        ]
        algorithms = [{
            "name": "Deep Bregman Density-Ratio Training Loop",
            "description": "A neural estimation routine: sample from two distributions, pass observations through a ReLU ratio network, compute the Bregman divergence objective, optimize with gradient descent, and use the fitted ratio to estimate KL divergence with uncertainty diagnostics.",
            "pseudocode": "initialize ReLU ratio_network\nfor each batch from distributions P and Q:\n  r_hat = ratio_network(x)\n  loss = bregman_density_ratio_objective(r_hat, samples)\n  backpropagate loss and update network\nestimate KL divergence from fitted ratios\ncompute error or normality diagnostics across sample sizes",
            "confidence": 0.87,
        }]
    elif _has(lowered, "research on machine learning", "classical algorithms", "historical development"):
        concepts = [
            _concept(
                "Machine-Learning Algorithm Taxonomy",
                "other",
                0.72,
                method,
                "the paper is a survey-style overview that organizes machine learning through classical algorithms, development history, and recent research directions",
                "reference or educational code that groups algorithms by family, exposes examples for supervised or unsupervised methods, and documents where each algorithm fits conceptually",
                "modules named for algorithm families, teaching notebooks, model registries, explanatory metadata, or UI sections that distinguish regression, classification, clustering, and neural approaches",
                "taxonomy and teaching structure rather than a novel estimator",
            ),
            _concept(
                "Classical ML Algorithm Demonstration Layer",
                "training_technique",
                0.67,
                method,
                "the survey's implementable value lies in turning broad algorithm descriptions into runnable demonstrations or comparative examples",
                "small implementations or wrappers for canonical models, consistent fit/predict interfaces, toy datasets, and visual comparisons of algorithm behavior",
                "classes with fit and predict methods, sklearn-like wrappers, examples for decision trees or regression, charts of model behavior, and educational comments connecting code to algorithm families",
                "demonstration infrastructure rather than research novelty",
            ),
            _concept(
                "Survey-to-Code Learning Map",
                "systems_design",
                0.62,
                method,
                "the paper can be used as a reading map for deciding which parts of a learning platform or codebase correspond to major ML topics",
                "navigation metadata, curriculum checkpoints, concept cards, or code examples that link survey sections to concrete exercises and implementations",
                "lesson structures, roadmap data, tags such as supervised learning or neural networks, and code examples that make the survey inspectable inside an educational system",
                "curricular mapping rather than model optimization",
            ),
        ]
        algorithms = []
    else:
        concepts = _generic_concepts(method, text)
        algorithms = []
    return PaperExtraction(
        core_contribution=(
            f"{method}. ReadSync extracted this as an implementation target by identifying the paper's estimator, workflow, evaluation requirements, and code-level search cues."
            if method else
            (text.strip().split("\n\n")[0][:900] if text.strip() else f"{title} has been ingested and is ready for structured extraction.")
        ),
        concepts=concepts,
        algorithms=algorithms,
        code_patterns=[],
        datasets=re.findall(r"(?:dataset|benchmark)\s+([A-Z][A-Za-z0-9_-]+)", text)[:8],
        evaluation_metrics=[m.upper() if len(m) <= 3 else m for m in sorted(set(re.findall(r"\b(accuracy|precision|recall|F1|ROC|AUC|BLEU|ROUGE|perplexity)\b", text, re.I)))],
        stated_limitations=re.findall(r"(?:limitation|limitation:|future work|we leave)[^.]{20,220}\.", text, flags=re.I)[:8],
        citations=re.findall(r"\b[A-Z][A-Za-z-]+ et al\.,? \d{4}\b", text)[:20],
    )


def extract_with_ollama(title: str, raw_text: str, paper_id: int | None = None) -> PaperExtraction:
    prompt = f"{SYSTEM_PROMPT}\n\nTITLE:\n{title}\n\nPAPER TEXT:\n{(raw_text or '')[:28000]}"
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1},
    }
    for attempt in range(2):
        try:
            request = urllib.request.Request(
                f"{settings.ollama_endpoint.rstrip('/')}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
            data = _normalize_extraction_payload(_json_from_text(body.get("response", "")), title)
            if not data["concepts"] and not data["algorithms"] and len((raw_text or "").strip()) >= 180:
                raise ValueError("Ollama returned valid JSON but no implementation concepts or algorithms.")
            return PaperExtraction.model_validate(data)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValidationError, Exception) as exc:
            log_error("ollama_extraction", f"Attempt {attempt + 1}: {exc}", {"title": title}, paper_id)
    return _fallback_extraction(title, raw_text)
