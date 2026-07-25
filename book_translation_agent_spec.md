# AI Book Translator Architecture Specification (Premium Architecture A)

## Core Philosophy
Achieve 100% flawless structural and linguistic translation of highly complex PDFs (books, resumes, manuals) with a $200/book budget, prioritizing perfection over processing time.

## Stage 1: Structural Extraction (MinerU / LlamaParse)
- **Engine**: MinerU (magic-pdf) high-accuracy vision parsing.
- **Process**: The PDF is visually parsed to detect columns, tables, headers, and bullet points.
- **Output**: A pristine, layout-preserving structural Markdown (`.md`) document.

## Stage 2: 4-Tier Translation & Cross-Verification Pipeline
1. **Tier 1 (Core Translation - OpenAI)**: GPT-4o directly translates the structural Markdown blocks. It inherently understands formatting tags and maintains the exact Markdown layout while translating the text.
2. **Tier 2 (Indic LLM Validation - Local)**: `ai4bharat/indictrans2-en-indic-1B` (CUDA-accelerated) independently generates a deterministic draft of the English text to act as a linguistic baseline for specialized Marathi syntax.
3. **Tier 3 (Local End-to-End QA - Ollama Qwen2.5)**: The local `qwen2.5:7b-instruct-q4_K_M` agent acts as the supreme editor. It compares the OpenAI translation against the IndicTrans2 baseline, resolving idioms, enforcing dictionary consistency (RAG), and verifying context.
4. **Tier 4 (Visual Anchor QA - OpenAI Vision)**: Randomly sampled screenshots of the original PDF pages are sent to the OpenAI Vision API. The model cross-references the screenshot against the final translated Markdown block to guarantee zero structural drift and perfect visual context alignment.

## Stage 3: Rendering & Reassembly
- **Engine**: A Markdown-to-PDF / Markdown-to-DOCX renderer.
- **Styling**: Injects custom CSS/XML rules to handle Devanagari Complex Script (`w:cs`) font rendering (e.g., Mangal or custom premium fonts).
- **Output**: A flawless, pixel-perfect, completely verified Marathi document.
