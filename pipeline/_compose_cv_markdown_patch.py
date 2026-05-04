def compose_cv_markdown(
    name:            str,
    contact:         "dict | str",
    section_content: dict,
) -> str:
    """
    Compose a full cv.md from per-section raw content.

    Follows SECTION_ORDER — missing sections are skipped with a warning.
    name and contact are passed separately as they are not section-managed.

    Args:
        name:            Full name from cv_data.
        contact:         Contact dict (email, phone, location, linkedin keys)
                         OR a raw pre-formatted contact string (e.g. parsed
                         from an existing cv.md header).
        section_content: dict[section_name -> raw section text].

    Returns:
        Full CV as a Markdown string.
    """
    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(f"# {name}")

    if isinstance(contact, str):
        lines.append(contact)
    else:
        contact_parts = [
            contact.get("email", ""),
            contact.get("phone", ""),
            contact.get("location", ""),
            contact.get("linkedin", ""),
        ]
        lines.append(" · ".join(p for p in contact_parts if p))

    lines += ["", "---", ""]

    # ── Sections ──────────────────────────────────────────────────────────────
    for section_name in SECTION_ORDER:
        content = section_content.get(section_name, "").strip()
        if not content:
            log.warning(f"Section '{section_name}' missing from content map — skipping.")
            continue

        label = SECTION_LABELS[section_name]
        lines.append(f"## {label}")
        lines.append("")
        lines.append(content)
        lines += ["", "---", ""]

    return "\n".join(lines)
