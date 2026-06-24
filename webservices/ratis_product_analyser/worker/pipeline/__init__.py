"""Pipeline v3 — receipt OCR pipeline rewrite per ARCH_receipt_pipeline.md.

Bloc 1 ships only the typed contract (Pydantic v2). Subsequent blocks
add the phase implementations (extract / comprehend / match / persist)
and DB migrations. No I/O, no SQL, no business logic in this package
yet — only immutable value objects and their invariants.
"""
