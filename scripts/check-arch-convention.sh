#!/usr/bin/env bash
# ============================================================
# Check the pipe-separated convention on long-lived docs.
# ============================================================
# Convention (CLAUDE.md R41) :
#   ## <ID> — <titre> · <refs> · <STATUT>
#   > TL;DR (1-2 phrases)
#   > @tags: mots espacés
#   > @subs: auto
#
# Where :
#   IDs    : DA-N / KP-N / HSP-N / M-N (or any `[A-Z]+-N`)
#   STATUT : LIVRÉ | EN-COURS | PLANIFIÉ | DEPRECATED (+ free suffix)
#
# This script SCANS the same file set as `generate-arch-inventory.py`
# and emits a `WARN: <file>:<line> — <id> non-conforme (<motif>)` line on
# stderr for every `## <ID> — …` heading that breaks the convention.
#
# Phase pédagogique : we ALWAYS exit 0 — the goal is to surface drift to
# the author, not block CI. After 2 sprints of warnings, the policy can
# flip to `exit 1` (out of Batch A scope — Batch B will revisit).
#
# Sources scanned :
#   - tracked ARCH_*.md (anywhere — incl. docs/arch/ post phase A relocate)
#   - docs/known/KNOWN_PROBLEMS.md, docs/decisions/DECISIONS_ACTED.md
#     (root paths also accepted for back-compat / synthetic test repos)
# Sources excluded :
#   - docs/superpowers/** · docs/audits/** · docs/product/**
#   - SESSION_LOG.md · PRODUCT.md · PRIVACY.md · TRAINING.md
#   - PROD_CHECKLIST.md · AUDIT_*.md
#   - CLAUDE.md · ORCHESTRATOR.md · SA_*.md · ARCH_INVENTORY.md
#
# Usage :
#   scripts/check-arch-convention.sh                # warn-only, exit 0
#   scripts/check-arch-convention.sh --github       # also emit ::warning:: lines for GH Actions annotations
# ============================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

GITHUB_MODE=0
if [[ "${1:-}" == "--github" ]]; then
    GITHUB_MODE=1
fi

is_excluded() {
    local path="$1"
    local name
    name="$(basename "$path")"
    case "$name" in
        ARCH_INVENTORY.md|SESSION_LOG.md|PRODUCT.md|PRIVACY.md|TRAINING.md|PROD_CHECKLIST.md|CLAUDE.md|ORCHESTRATOR.md)
            return 0 ;;
        SA_*.md|AUDIT_*.md)
            return 0 ;;
    esac
    case "$path" in
        docs/superpowers/*|docs/audits/*|docs/product/*)
            return 0 ;;
    esac
    return 1
}

# Match a fully conforming heading.
# Group: ID, title, refs, base STATUS, optional suffix.
CONFORM_RE='^## ([A-Z]+-[0-9]+) — .+ · .+ · (LIVRÉ|EN-COURS|PLANIFIÉ|DEPRECATED)( .*)?$'
# Match any heading that LOOKS like a convention attempt (`## ID — …`).
PREFIX_RE='^## ([A-Z]+-[0-9]+) — .*$'

emit_warn() {
    local file="$1" line="$2" id="$3" motif="$4"
    echo "WARN: ${file}:${line} — ${id} non-conforme (${motif})" >&2
    if (( GITHUB_MODE == 1 )); then
        # GitHub Actions inline annotation.
        printf '::warning file=%s,line=%s::%s non-conforme (%s)\n' "$file" "$line" "$id" "$motif"
    fi
}

check_file() {
    local file="$1"
    local line_no=0
    local heading_line_no=0
    local heading_id=""
    local heading_text=""
    local state="idle"  # idle | expect_meta
    local saw_tldr=0 saw_tags=0 saw_subs=0

    flush_heading() {
        # Called when we leave a heading region (next `## ` or EOF) — check
        # that we collected TL;DR / @tags / @subs for the previous heading.
        if [[ -n "$heading_id" ]]; then
            local missing=()
            (( saw_tldr == 0 )) && missing+=("TL;DR manquant")
            (( saw_tags == 0 )) && missing+=("@tags manquant")
            (( saw_subs == 0 )) && missing+=("@subs manquant")
            if (( ${#missing[@]} > 0 )); then
                emit_warn "$file" "$heading_line_no" "$heading_id" "$(IFS='; '; echo "${missing[*]}")"
            fi
        fi
        heading_id=""
        heading_line_no=0
        saw_tldr=0
        saw_tags=0
        saw_subs=0
        state="idle"
    }

    while IFS= read -r line; do
        line_no=$((line_no + 1))

        if [[ "$line" =~ ^"## " ]]; then
            # Flush previous heading metadata check.
            flush_heading

            if [[ "$line" =~ $CONFORM_RE ]]; then
                heading_id="${BASH_REMATCH[1]}"
                heading_text="$line"
                heading_line_no=$line_no
                state="expect_meta"
            elif [[ "$line" =~ $PREFIX_RE ]]; then
                # Looks like a convention attempt but heading line is malformed.
                local bad_id="${BASH_REMATCH[1]}"
                # If it has a "·" separator or contains a status keyword, it's
                # almost certainly an attempt — warn.
                if [[ "$line" == *" · "* ]] \
                   || [[ "$line" == *"LIVRÉ"* ]] \
                   || [[ "$line" == *"EN-COURS"* ]] \
                   || [[ "$line" == *"PLANIFIÉ"* ]] \
                   || [[ "$line" == *"DEPRECATED"* ]]; then
                    emit_warn "$file" "$line_no" "$bad_id" "titre malformé — attendu '## $bad_id — titre · refs · STATUT'"
                else
                    # Legacy heading without separator (e.g. `## DA-46 — title (date)`).
                    emit_warn "$file" "$line_no" "$bad_id" "legacy — manque '· refs · STATUT' (migration pending)"
                fi
            fi
            continue
        fi

        if [[ "$state" == "expect_meta" ]]; then
            # Skip blank lines after the heading until the quote block.
            if [[ -z "$line" ]]; then
                continue
            fi
            if [[ "$line" =~ ^">" ]]; then
                if [[ "$line" =~ ^">"[[:space:]]*"@tags:" ]]; then
                    saw_tags=1
                elif [[ "$line" =~ ^">"[[:space:]]*"@subs:" ]]; then
                    saw_subs=1
                else
                    # Plain quote line = TL;DR.
                    if (( saw_tldr == 0 )); then
                        saw_tldr=1
                    fi
                fi
            else
                # Quote block ended without finishing metadata; stop collecting.
                state="post_meta"
            fi
        fi
    done < "$file"

    # Flush final heading after EOF.
    flush_heading
}

main() {
    local checked=0
    # Use git ls-files (tracked only) — matches the Python script discovery.
    # Bash 3.2 (default macOS) lacks `mapfile`, so we pipe directly.
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        if is_excluded "$f"; then
            continue
        fi
        [[ -f "$f" ]] || continue
        check_file "$f"
        checked=$((checked + 1))
    done < <(
        git ls-files \
            ':(glob)**/ARCH_*.md' 'ARCH_*.md' \
            'KNOWN_PROBLEMS.md' 'DECISIONS_ACTED.md' \
            'docs/known/KNOWN_PROBLEMS.md' \
            'docs/decisions/DECISIONS_ACTED.md' \
            | sort -u
    )

    echo "check-arch-convention: scanned ${checked} doc(s) (warn-only — exit 0)" >&2
    return 0
}

main "$@"
