---
name: uv-pep508-platform-marker-de-morgan
description: "Skip a uv/pip dependency on a specific platform (e.g. paddleocr on linux-aarch64) when the PEP 508 environment marker won't parse — PEP 508 forbids negating a parenthesised group, so rewrite the negation via De Morgan."
---

# uv-pep508-platform-marker-de-morgan

You want a dependency installed everywhere *except* one platform — e.g.
`paddleocr` has no wheel on `linux-aarch64` and you want it skipped there.
The intuitive marker `not (sys_platform == 'linux' and platform_machine
== 'aarch64')` fails to parse : PEP 508 does **not** allow negating a
parenthesised group. The fix is to distribute the negation with De
Morgan's law into a flat `!=` / `or` expression that the parser accepts.

## When to Use

- You need to exclude a dependency on one specific platform in
  `pyproject.toml` / requirements via an environment marker.
- A marker raises `ValueError: ... must be pep508` or otherwise fails to
  parse, and it contains a negated parenthesised group `not (...)`.

## When NOT to Use

- The exclusion can be handled natively by uv groups / extras or
  `[tool.uv]` overrides — prefer the native mechanism over a hand-written
  marker.
- The condition is a simple single comparison with no compound negation —
  no De Morgan needed, just write the `==` / `!=` directly.

## Procedure

1. **Recognise the cause.** PEP 508 markers cannot negate a parenthesised
   group. `not (A and B)` is invalid.
2. **Apply De Morgan to distribute the negation.**
   - `not (A and B)` → `not A or not B`
   - Example : `not (sys_platform == 'linux' and platform_machine ==
     'aarch64')` becomes
     `sys_platform != 'linux' or platform_machine != 'aarch64'`.
3. **Use only valid marker operators.** `==` / `!=` on marker variables
   (`sys_platform`, `platform_machine`, `python_version`, …) joined by
   `and` / `or`. No `not (...)`, no negated parentheses.
4. **Add a runtime backstop.** Guard the code so the missing dependency
   doesn't break the suite on the excluded platform — e.g.
   `pytest.importorskip("paddleocr")` or a lazy import that degrades
   cleanly.
5. **Document the marker.** Leave a one-line comment explaining why the
   platform is excluded, so a later edit doesn't "simplify" it back into
   the invalid `not (...)` form.
