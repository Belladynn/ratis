# ADR-0013: Agentic doc RAG — hybrid bge-m3 + sqlite-vec behind a typed MCP surface

**Status:** Accepted (Module 9; phase D vector + phase E session bridge)

## Context and Problem Statement

Project rule R29 forbids agents from full-reading large ARCH/known-problems docs (token cost, drift). Agents needed a way to find the right section *by meaning*, not just exact keywords, without knowing file paths or line ranges. How does an agent retrieve the right bounded doc section without reading whole files or hardcoding paths?

## Decision Drivers

- Encapsulate the R29 reading discipline so an agent never full-reads a large doc.
- Retrieve by meaning (semantic) while preserving exact-tag (keyword) matches.
- No external provider, no Keychain entry — everything local, RGPD-safe.
- Stay lightweight: the corpus is small (~80 vectors), so no heavy vector DB.
- Graceful degradation if heavy embedding models are unavailable.

## Considered Options

- **`docs_tools` (Module 9): typed MCP calls over a hybrid bge-m3 + sqlite-vec backend.**
- **Rewrite the index as a heavyweight LlamaIndex / external vector DB.**
- **A full virtual-table `vec0`.**
- **Plain keyword-only grep.**

## Decision Outcome

Chosen: add `docs_tools` (Module 9) that indexes the pipe-separated `ARCH_INVENTORY` (R41 convention) and serves `docs_search/get/find/list_files/reindex`. Phase D upgrades the backend to **hybrid retrieval**: bge-m3 embeddings (sentence-transformers) in `sqlite-vec`, fused as `0.7*vector + 0.3*keyword`, with the public tool surface unchanged. A session-context bridge (phase E) extends the corpus to decisions, known-problems, postmortems, active skills, and user memory, and injects relevant nuggets at SessionStart via a hook. Cosine over ~80 vectors in numpy is trivial (<200 ms), so no heavy vector DB is needed; everything is local repo reads.

**Rejected:** heavyweight LlamaIndex / external vector DB (project memory explicitly says do NOT rewrite docs-mcp in LlamaIndex); a full virtual-table `vec0` (deferred until the corpus exceeds thousands of entries); plain keyword-only grep (kept only as a graceful-degradation fallback).

**Quality-attribute trade-off:** we bought **usability and privacy** (the R29 discipline encapsulated in 4 typed calls, semantic + keyword retrieval, no doc content leaving the machine) at the cost of **a heavy model footprint and limited V1 coverage** — bge-m3 is ~600 MB to load, and the vector index in V1 covers `ARCH_INVENTORY` only (multi-source filtering routes to the keyword backend).

### Consequences

- **Good:** cheap, local, RGPD-safe (no doc content leaves the machine); graceful degradation (bge-m3 → MiniLM → keyword-only if sentence-transformers missing); freshness check against inventory mtime; the agent asks for HSP-3 and gets a bounded section without knowing the path or line range.
- **Bad:** bge-m3 is ~600 MB to load; the V1 vector index covers `ARCH_INVENTORY` only (multi-source filtering routes to keyword); index freshness depends on the session-start hook running.

**Source.** `docs/arch/ARCH_agent_mcp.md` Module 9 (docs_tools, phase D vector + phase E session bridge); `CLAUDE.md` R28/R41; DS-4. Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
