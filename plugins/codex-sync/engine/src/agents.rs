use anyhow::{Context, Result};

use crate::model::ExternalAgentsSection;

fn section_range(source: &str, section: &ExternalAgentsSection) -> Result<Option<(usize, usize)>> {
    let begins: Vec<_> = source.match_indices(&section.begin_marker).collect();
    let ends: Vec<_> = source.match_indices(&section.end_marker).collect();
    match (begins.as_slice(), ends.as_slice()) {
        ([], []) => Ok(None),
        ([(start, _)], [(end, _)]) if start < end => {
            Ok(Some((*start, end + section.end_marker.len())))
        }
        _ => anyhow::bail!(
            "external AGENTS section {} has duplicate, unmatched, or reversed markers",
            section.id
        ),
    }
}

fn section_ranges(
    source: &str,
    sections: &[ExternalAgentsSection],
) -> Result<Vec<(usize, usize, String)>> {
    let mut ranges = Vec::new();
    for section in sections {
        if let Some((start, end)) = section_range(source, section)? {
            ranges.push((start, end, section.id.clone()));
        }
    }
    ranges.sort_by_key(|(start, _, _)| *start);
    for pair in ranges.windows(2) {
        let (_, previous_end, previous_id) = &pair[0];
        let (next_start, _, next_id) = &pair[1];
        if next_start < previous_end {
            anyhow::bail!("external AGENTS sections {previous_id} and {next_id} overlap");
        }
    }
    Ok(ranges)
}

pub fn strip_external_sections(
    source: &[u8],
    sections: &[ExternalAgentsSection],
) -> Result<Vec<u8>> {
    let mut text = std::str::from_utf8(source)
        .context("global AGENTS.md is not valid UTF-8")?
        .to_owned();
    let ranges = section_ranges(&text, sections)?;
    for (start, end, _) in ranges.into_iter().rev() {
        let prefix = text[..start].trim_end_matches([' ', '\t', '\r', '\n']);
        let suffix = text[end..].trim_start_matches([' ', '\t', '\r', '\n']);
        text = match (prefix.is_empty(), suffix.is_empty()) {
            (true, true) => String::new(),
            (false, true) => format!("{prefix}\n"),
            (true, false) => format!("{suffix}\n"),
            (false, false) => format!("{prefix}\n\n{suffix}\n"),
        };
    }
    Ok(text.into_bytes())
}

pub fn render_with_external_sections(
    canonical: &[u8],
    current: &[u8],
    sections: &[ExternalAgentsSection],
) -> Result<Vec<u8>> {
    if sections.is_empty() {
        return Ok(canonical.to_vec());
    }
    let canonical_text =
        std::str::from_utf8(canonical).context("synchronized AGENTS.md is not valid UTF-8")?;
    let current_text =
        std::str::from_utf8(current).context("global AGENTS.md is not valid UTF-8")?;
    let mut external = Vec::new();
    for section in sections {
        if section_range(canonical_text, section)?.is_some() {
            anyhow::bail!(
                "synchronized AGENTS.md must not contain externally managed section {}",
                section.id
            );
        }
    }
    section_ranges(canonical_text, sections)?;
    for (start, end, _) in section_ranges(current_text, sections)? {
        external.push(current_text[start..end].to_owned());
    }
    let mut rendered = canonical_text.trim_end().to_owned();
    for block in external {
        if !rendered.is_empty() {
            rendered.push_str("\n\n");
        }
        rendered.push_str(&block);
    }
    if !rendered.is_empty() {
        rendered.push('\n');
    }
    Ok(rendered.into_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fastctx() -> ExternalAgentsSection {
        ExternalAgentsSection {
            id: "fastctx".to_owned(),
            begin_marker: "<!-- fastctx:begin -->".to_owned(),
            end_marker: "<!-- fastctx:end -->".to_owned(),
        }
    }

    #[test]
    fn render_preserves_live_external_section() {
        let rendered = render_with_external_sections(
            b"# Canonical\n",
            b"# Local\n\n<!-- fastctx:begin -->\nFastCtx\n<!-- fastctx:end -->\n",
            &[fastctx()],
        )
        .unwrap();
        assert_eq!(
            rendered,
            b"# Canonical\n\n<!-- fastctx:begin -->\nFastCtx\n<!-- fastctx:end -->\n"
        );
    }

    #[test]
    fn strip_removes_external_section_from_capture() {
        let stripped = strip_external_sections(
            b"# Local\n\n<!-- fastctx:begin -->\nFastCtx\n<!-- fastctx:end -->\n",
            &[fastctx()],
        )
        .unwrap();
        assert_eq!(stripped, b"# Local\n");
    }

    #[test]
    fn duplicate_markers_are_rejected() {
        let error = render_with_external_sections(
            b"# Canonical\n",
            b"<!-- fastctx:begin --><!-- fastctx:begin --><!-- fastctx:end -->",
            &[fastctx()],
        )
        .unwrap_err();
        assert!(error.to_string().contains("duplicate"));
    }

    #[test]
    fn overlapping_sections_are_rejected() {
        let sections = [
            fastctx(),
            ExternalAgentsSection {
                id: "other".to_owned(),
                begin_marker: "<!-- other:begin -->".to_owned(),
                end_marker: "<!-- other:end -->".to_owned(),
            },
        ];
        let error = render_with_external_sections(
            b"# Canonical\n",
            b"<!-- fastctx:begin -->\n<!-- other:begin -->\n<!-- fastctx:end -->\n<!-- other:end -->\n",
            &sections,
        )
        .unwrap_err();
        assert!(error.to_string().contains("overlap"));
    }
}
