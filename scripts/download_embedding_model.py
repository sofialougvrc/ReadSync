#!/usr/bin/env python3
import os

os.environ["READSYNC_ALLOW_MODEL_DOWNLOAD"] = "1"

from sentence_transformers import SentenceTransformer


def main():
    model_name = "sentence-transformers/all-MiniLM-L6-v2"
    print(f"Downloading/caching {model_name}...")
    SentenceTransformer(model_name)
    print("Embedding model is cached. ReadSync will use sentence-transformers for future indexing and matching.")


if __name__ == "__main__":
    main()
