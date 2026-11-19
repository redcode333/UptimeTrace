"""Adapters that turn externally published anomaly-detection datasets into a common, provenance-tracked layout.

Principles (docs/ML_EXTERNAL_DATASETS.md):
  * Raw files are never modified; adapters only read them and refuse to write inside the raw tree.
  * Normalisation changes representation only (float32 columns, int8 labels, explicit row index). NO scaling, NO imputation:
    missing values stay NaN (never zero), because "no data" and "low usage" must stay distinguishable.
  * Every output set carries a manifest: source repository + commit, archive and file checksums, retrieval time, adapter version,
    licence and attribution text, row counts. Dataset-specific feature and label mappings live in one module per dataset.
  * Datasets with different feature definitions or label semantics are never merged.
"""
ADAPTER_VERSION = "1.0"
